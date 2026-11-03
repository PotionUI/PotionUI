import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { posix, resolve } from 'node:path';

const REPO = resolve(__dirname, '../../../..');
const PLUGINS_ROOT = 'content/plugins';

interface Instruction {
	keyword: string;
	args: string;
}

interface Stage {
	name: string;
	instructions: Instruction[];
}

function read(path: string): string {
	return readFileSync(resolve(REPO, path), 'utf-8').replace(/\r\n/g, '\n');
}

function parseDockerfile(source: string): Stage[] {
	const stages: Stage[] = [];
	let pending = '';
	for (const line of source.split('\n')) {
		const trimmed = line.trim();
		if (!pending && (!trimmed || trimmed.startsWith('#'))) continue;
		if (trimmed.endsWith('\\')) {
			pending += `${trimmed.slice(0, -1).trim()} `;
			continue;
		}
		const text = pending + trimmed;
		pending = '';
		const [keyword] = text.split(/\s+/, 1);
		const instruction = { keyword: keyword.toUpperCase(), args: text.slice(keyword.length).trim() };
		if (instruction.keyword === 'FROM') {
			stages.push({ name: instruction.args.split(/\s+as\s+/i)[1] ?? '', instructions: [] });
		}
		stages.at(-1)?.instructions.push(instruction);
	}
	return stages;
}

function escapeRegex(text: string): string {
	return text.replace(/[.+^$()|\\]/g, '\\$&');
}

function globRegex(pattern: string): RegExp {
	let source = '';
	for (let i = 0; i < pattern.length; i++) {
		const char = pattern[i];
		if (char === '*' && pattern[i + 1] === '*') {
			i++;
			if (pattern[i + 1] === '/') {
				i++;
				source += '(?:.*/)?';
			} else {
				source += '.*';
			}
		} else if (char === '*') source += '[^/]*';
		else if (char === '?') source += '[^/]';
		else if (char === '{') source += '(?:';
		else if (char === '}') source += ')';
		else if (char === ',') source += '|';
		else source += escapeRegex(char);
	}
	return new RegExp(`^${source}$`);
}

interface IgnoreRule {
	negated: boolean;
	regex: RegExp;
}

function parseDockerignore(source: string): IgnoreRule[] {
	return source
		.split('\n')
		.map((line) => line.trim())
		.filter((line) => line && !line.startsWith('#'))
		.map((line) => {
			const negated = line.startsWith('!');
			const pattern = (negated ? line.slice(1) : line).replace(/^\/+/, '').replace(/\/+$/, '');
			return { negated, regex: globRegex(pattern) };
		});
}

function isIgnored(rules: IgnoreRule[], path: string): boolean {
	const segments = path.split('/');
	const candidates = segments.map((_, i) => segments.slice(0, i + 1).join('/'));
	let ignored = false;
	for (const rule of rules) {
		if (candidates.some((candidate) => rule.regex.test(candidate))) ignored = !rule.negated;
	}
	return ignored;
}

function walk(dir: string): string[] {
	const files: string[] = [];
	for (const entry of readdirSync(resolve(REPO, dir), { withFileTypes: true })) {
		const path = `${dir}/${entry.name}`;
		if (entry.isDirectory()) {
			if (entry.name !== 'node_modules') files.push(...walk(path));
		} else {
			files.push(path);
		}
	}
	return files;
}

let stages: Stage[];
let frontend: Stage;
let workdir: string;
let pluginGlobs: string[];
let pluginFiles: string[];

beforeAll(async () => {
	stages = parseDockerfile(read('docker/Dockerfile'));
	frontend = stages.find((stage) => stage.instructions.some((i) => i.keyword === 'RUN' && i.args === 'npm run build'))!;
	workdir = frontend.instructions.filter((i) => i.keyword === 'WORKDIR').at(-1)!.args;
	const config = await import(/* @vite-ignore */ resolve(REPO, 'frontend/tailwind.config.js'));
	pluginGlobs = (config.default.content as string[])
		.filter((glob) => glob.startsWith(`../${PLUGINS_ROOT}/`))
		.map((glob) => glob.slice(3));
	pluginFiles = walk(PLUGINS_ROOT);
});

function scannedBy(glob: string): string[] {
	const regex = globRegex(glob);
	return pluginFiles.filter((file) => regex.test(file));
}

function keptRoot(glob: string): string {
	const segments = glob.slice(PLUGINS_ROOT.length + 1).split('/');
	const end = segments.indexOf('**');
	return segments.slice(0, end === -1 ? -1 : end).join('/');
}

function sourceStage(): { stage: Stage; copy: Instruction } {
	const copy = frontend.instructions.find(
		(i) => i.keyword === 'COPY' && i.args.startsWith('--from=') && /\s\/content\s*$/.test(i.args)
	);
	expect(copy, 'COPY --from=<stage> ... /content in the frontend stage').toBeDefined();
	const name = copy!.args.match(/^--from=(\S+)/)![1];
	const stage = stages.find((candidate) => candidate.name === name);
	expect(stage, `stage ${name}`).toBeDefined();
	return { stage: stage!, copy: copy! };
}

function stageFiles(stage: Stage): string[] {
	const copy = stage.instructions.find((i) => i.keyword === 'COPY' && i.args.startsWith(`${PLUGINS_ROOT} `));
	expect(copy, `COPY ${PLUGINS_ROOT} <staging> in ${stage.name}`).toBeDefined();
	const staging = copy!.args.split(/\s+/)[1];
	const run = stage.instructions.find((i) => i.keyword === 'RUN' && i.args.includes(`cd ${staging}`));
	expect(run, `RUN cd ${staging} in ${stage.name}`).toBeDefined();
	expect(run!.args).toContain('cp -a "$src/." "/content/plugins/$src/"');
	expect(run!.args).toMatch(/find \/content\/plugins -type d -name node_modules -prune -exec rm -rf \{\} \+/);
	const kept = run!.args.match(/for src in (.+?); do/)![1].split(/\s+/);
	return kept.flatMap((root) => {
		const regex = globRegex(`${PLUGINS_ROOT}/${root}/**`);
		return pluginFiles.filter((file) => regex.test(file) && !file.startsWith(`${PLUGINS_ROOT}/node_modules/`));
	});
}

describe('docker/Dockerfile ships the plugin sources Tailwind scans', () => {
	it('reads the plugin globs of the Tailwind config', () => {
		expect(pluginGlobs).toEqual(
			expect.arrayContaining([
				'content/plugins/sdk/**/*.svelte',
				'content/plugins/*/*/frontend/src/**/*.{svelte,js,ts}'
			])
		);
		expect(workdir).toBe('/frontend');
	});

	it('copies the plugin sources into the frontend stage before the build, where ../content resolves', () => {
		const { copy } = sourceStage();
		const copyIndex = frontend.instructions.indexOf(copy);
		const buildIndex = frontend.instructions.findIndex((i) => i.keyword === 'RUN' && i.args === 'npm run build');
		expect(copyIndex).toBeLessThan(buildIndex);
		for (const glob of pluginGlobs) {
			expect(posix.join(workdir, '..', glob).startsWith('/content/')).toBe(true);
		}
	});

	it('keeps every file each plugin glob matches, at the same path', () => {
		const kept = new Set(stageFiles(sourceStage().stage));
		for (const glob of pluginGlobs) {
			const scanned = scannedBy(glob);
			expect(scanned.length, glob).toBeGreaterThan(0);
			expect(scanned.filter((file) => !kept.has(file)), glob).toEqual([]);
		}
	});

	it('keeps only the trees the globs scan: no node_modules, no dist, no backend', () => {
		const files = stageFiles(sourceStage().stage);
		const roots = pluginGlobs.map((glob) => globRegex(`${PLUGINS_ROOT}/${keptRoot(glob)}/**`));
		expect(files.filter((file) => !roots.some((root) => root.test(file)))).toEqual([]);
		expect(files.filter((file) => /\/(node_modules|dist|backend)\//.test(file))).toEqual([]);
	});
});

describe('.dockerignore', () => {
	const rules = () => parseDockerignore(read('.dockerignore'));

	it('sends every plugin source Tailwind scans to the build', () => {
		for (const glob of pluginGlobs) {
			expect(scannedBy(glob).filter((file) => isIgnored(rules(), file)), glob).toEqual([]);
		}
	});

	it('leaves out plugin node_modules at any depth but keeps what the runtime serves', () => {
		expect(isIgnored(rules(), 'content/plugins/node_modules/esbuild/package.json')).toBe(true);
		expect(isIgnored(rules(), 'content/plugins/marketplace/x/frontend/node_modules/dep/index.js')).toBe(true);
		expect(isIgnored(rules(), 'content/plugins/marketplace/x/frontend/dist/index.js')).toBe(false);
		expect(isIgnored(rules(), 'content/plugins/marketplace/x/backend/routes.py')).toBe(false);
		expect(isIgnored(rules(), 'content/plugins/marketplace/x/manifest.yml')).toBe(false);
	});

	it('is matched from the context root, as Docker does', () => {
		const rootOnly = parseDockerignore('node_modules/\n*.md\n!docs/keep.md');
		expect(isIgnored(rootOnly, 'node_modules/x.js')).toBe(true);
		expect(isIgnored(rootOnly, 'content/plugins/node_modules/x.js')).toBe(false);
		expect(isIgnored(rootOnly, 'README.md')).toBe(true);
		expect(isIgnored(rootOnly, 'docs/guide.md')).toBe(false);
		expect(isIgnored(parseDockerignore('**/*.md\n!docs/keep.md'), 'docs/keep.md')).toBe(false);
	});
});
