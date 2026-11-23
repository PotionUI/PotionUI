import { existsSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { gzipSync } from 'node:zlib';

export const ROOT_SUFFIXES = [
	'/client-optimized/nodes/0.js',
	'/client-optimized/app.js',
	'/runtime/client/entry.js'
];

export const ROUTES = ['/generate'];

const HEADROOM = 1.03;
const TIGHTEN_RATIO = 0.9;
const TOP_CHUNKS = 10;

export function findRoots(manifest, suffixes = ROOT_SUFFIXES) {
	return suffixes.map((suffix) => {
		const key = Object.keys(manifest).find((candidate) => candidate.replace(/\\/g, '/').endsWith(suffix));
		if (!key) throw new Error(`manifest has no entry ending in ${suffix}; the build layout changed`);
		return key;
	});
}

export function staticClosure(manifest, roots) {
	const seen = new Set();
	const stack = [...roots];
	while (stack.length) {
		const key = stack.pop();
		if (seen.has(key)) continue;
		const chunk = manifest[key];
		if (!chunk) throw new Error(`manifest entry ${key} is referenced but missing`);
		seen.add(key);
		for (const next of chunk.imports ?? []) stack.push(next);
	}
	return [...seen];
}

export function measure(manifest, sizeOf, roots = findRoots(manifest)) {
	const keys = staticClosure(manifest, roots);
	const jsFiles = new Map();
	const cssFiles = new Set();
	for (const key of keys) {
		const chunk = manifest[key];
		if (chunk.file) jsFiles.set(chunk.file, key);
		for (const css of chunk.css ?? []) cssFiles.add(css);
	}
	const js = [...jsFiles.keys()].map((file) => ({ file, key: jsFiles.get(file), bytes: sizeOf(file) }));
	const css = [...cssFiles].map((file) => ({ file, bytes: sizeOf(file) }));
	const sum = (items) => items.reduce((total, item) => total + item.bytes, 0);
	return { chunks: js.length, js, css, js_raw: sum(js), css_raw: sum(css) };
}

export function routeNodeKey(manifest, appSource, route) {
	const escaped = route.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const match = new RegExp(`"${escaped}":\\[(\\d+)`).exec(appSource);
	if (!match) throw new Error(`the app entry has no node for route ${route}; the routing table changed`);
	return findRoots(manifest, [`/client-optimized/nodes/${match[1]}.js`])[0];
}

export function measureRoute(manifest, sizeOf, appSource, route, shell) {
	const shellJs = new Set(shell.js.map((item) => item.file));
	const shellCss = new Set(shell.css.map((item) => item.file));
	const full = measure(manifest, sizeOf, [routeNodeKey(manifest, appSource, route)]);
	const js = full.js.filter((item) => !shellJs.has(item.file));
	const css = full.css.filter((item) => !shellCss.has(item.file));
	const sum = (items) => items.reduce((total, item) => total + item.bytes, 0);
	return { chunks: js.length, js, css, js_raw: sum(js), css_raw: sum(css) };
}

function checkLimits(label, result, limits, failures, notes) {
	for (const metric of ['js_raw', 'css_raw']) {
		const limit = limits?.[metric];
		if (typeof limit !== 'number') throw new Error(`bundle-budget.json is missing ${label}${metric}`);
		if (result[metric] > limit) failures.push(`${label}${metric}`);
		else if (result[metric] < limit * TIGHTEN_RATIO) notes.push(`${label}${metric}`);
	}
}

export function evaluate(result, budget, routeResults = {}) {
	const failures = [];
	const notes = [];
	checkLimits('', result, budget, failures, notes);
	for (const [route, routeResult] of Object.entries(routeResults)) {
		checkLimits(`${route} `, routeResult, budget.routes?.[route], failures, notes);
	}
	return { failures, notes };
}

export function nextBudget(result, previous = {}, routeResults = {}) {
	const round = (bytes) => Math.ceil((bytes * HEADROOM) / 1024) * 1024;
	const routes = {};
	for (const [route, routeResult] of Object.entries(routeResults)) {
		routes[route] = { js_raw: round(routeResult.js_raw), css_raw: round(routeResult.css_raw) };
	}
	return { js_raw: round(result.js_raw), css_raw: round(result.css_raw), routes, target: previous.target ?? {} };
}

const kb = (bytes) => `${(bytes / 1024).toFixed(1)} KB`;
const num = (value) => value.toLocaleString('en-US');

function parseArgs(argv) {
	const args = { dir: null, budget: null, write: false };
	for (let i = 0; i < argv.length; i++) {
		if (argv[i] === '--write') args.write = true;
		else if (argv[i] === '--dir') args.dir = argv[++i];
		else if (argv[i] === '--budget') args.budget = argv[++i];
	}
	return args;
}

function run(args, frontend) {
	const buildRoot = process.env.E2E_BUILD_DIR ? resolve(process.env.E2E_BUILD_DIR) : frontend;
	const dir = resolve(args.dir ?? join(buildRoot, '.svelte-kit', 'output', 'client'));
	const manifestPath = join(dir, '.vite', 'manifest.json');
	if (!existsSync(manifestPath)) {
		console.error(`No build manifest at ${manifestPath}. Run \`npm run build\` first.`);
		return 2;
	}
	const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
	const budgetPath = resolve(args.budget ?? join(frontend, 'bundle-budget.json'));
	const sizeOf = (file) => statSync(join(dir, file)).size;
	const result = measure(manifest, sizeOf);
	const appFile = manifest[findRoots(manifest, ['/client-optimized/app.js'])[0]].file;
	const appSource = readFileSync(join(dir, appFile), 'utf8');
	const routeResults = {};
	for (const route of ROUTES) routeResults[route] = measureRoute(manifest, sizeOf, appSource, route, result);
	const gzip = (items) => items.reduce((total, item) => total + gzipSync(readFileSync(join(dir, item.file))).length, 0);

	if (args.write) {
		const previous = existsSync(budgetPath) ? JSON.parse(readFileSync(budgetPath, 'utf8')) : {};
		const next = nextBudget(result, previous, routeResults);
		writeFileSync(budgetPath, `${JSON.stringify(next, null, '\t')}\n`);
		console.log(`Wrote ${budgetPath}`);
		return 0;
	}

	if (!existsSync(budgetPath)) {
		console.error(`No budget file at ${budgetPath}. Create it with \`node scripts/check-bundle-budget.mjs --write\`.`);
		return 2;
	}
	const budget = JSON.parse(readFileSync(budgetPath, 'utf8'));
	const { failures, notes } = evaluate(result, budget, routeResults);
	const line = (label, value, limit) => {
		const status = failures.includes(label.trim()) ? 'OVER BUDGET' : 'OK';
		console.log(`${label.padEnd(18)} ${num(value)} / ${num(limit)}  ${status}`);
	};
	for (const metric of ['js_raw', 'css_raw']) line(metric, result[metric], budget[metric]);
	for (const [route, routeResult] of Object.entries(routeResults)) {
		for (const metric of ['js_raw', 'css_raw']) line(`${route} ${metric}`, routeResult[metric], budget.routes[route][metric]);
	}
	console.log(`gzip     shell js ${num(gzip(result.js))}, css ${num(gzip(result.css))} (informational)`);
	for (const [route, routeResult] of Object.entries(routeResults)) {
		console.log(`gzip     ${route} js ${num(gzip(routeResult.js))}, css ${num(gzip(routeResult.css))} (informational)`);
	}
	console.log(`closure  shell ${result.chunks} JS chunks, ${result.css.length} CSS files`);
	for (const [route, routeResult] of Object.entries(routeResults)) {
		console.log(`closure  ${route} adds ${routeResult.chunks} JS chunks, ${routeResult.css.length} CSS files`);
	}
	const target = budget.target ?? {};
	for (const metric of ['js_raw', 'css_raw']) {
		if (typeof target[metric] === 'number' && result[metric] > target[metric]) {
			console.log(`target   shell ${metric} is ${kb(result[metric] - target[metric])} over the ${kb(target[metric])} target`);
		}
	}
	const largest = (label, items) => {
		console.log(`largest chunks, ${label}:`);
		for (const item of [...items].sort((a, b) => b.bytes - a.bytes).slice(0, TOP_CHUNKS)) {
			console.log(`  ${num(item.bytes).padStart(10)}  ${item.file}`);
		}
	};
	largest('shell', [...result.js, ...result.css]);
	for (const [route, routeResult] of Object.entries(routeResults)) largest(`${route} beyond the shell`, [...routeResult.js, ...routeResult.css]);
	for (const metric of notes) {
		console.log(`headroom ${metric} is more than 10% under its limit; tighten bundle-budget.json`);
	}
	if (failures.length) {
		console.error(`Bundle budget exceeded: ${failures.join(', ')}. Shrink what grew or raise the budget deliberately with \`node scripts/check-bundle-budget.mjs --write\`.`);
		return 1;
	}
	return 0;
}

export function main(argv = process.argv.slice(2)) {
	const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
	try {
		return run(parseArgs(argv), frontend);
	} catch (error) {
		console.error(error.message);
		return 2;
	}
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
	process.exit(main());
}
