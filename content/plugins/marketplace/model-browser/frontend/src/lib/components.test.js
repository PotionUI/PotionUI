import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const srcDir = join(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(join(srcDir, 'index.js'));
const { compile } = require('svelte/compiler');
const { parse } = require('acorn');

const KNOWN_GLOBALS = new Set([
	...Object.getOwnPropertyNames(globalThis),
	'window',
	'document',
	'navigator',
	'location',
	'localStorage',
	'sessionStorage',
	'requestAnimationFrame',
	'cancelAnimationFrame',
	'HTMLElement',
	'HTMLInputElement',
	'history',
	'Element',
	'Node',
	'Event',
	'KeyboardEvent',
	'MouseEvent',
	'FocusEvent',
	'ResizeObserver',
	'IntersectionObserver',
	'MutationObserver',
	'matchMedia',
	'getComputedStyle',
	'fetch',
	'FormData',
	'Image',
	'CustomEvent',
	'DOMParser',
	'$$props'
]);

function collectPatternNames(node, out) {
	if (!node) return;
	switch (node.type) {
		case 'Identifier':
			out.add(node.name);
			break;
		case 'ObjectPattern':
			for (const prop of node.properties) {
				collectPatternNames(prop.type === 'RestElement' ? prop.argument : prop.value, out);
			}
			break;
		case 'ArrayPattern':
			for (const element of node.elements) collectPatternNames(element, out);
			break;
		case 'RestElement':
			collectPatternNames(node.argument, out);
			break;
		case 'AssignmentPattern':
			collectPatternNames(node.left, out);
			break;
	}
}

function freeIdentifiers(code) {
	const ast = parse(code, { ecmaVersion: 'latest', sourceType: 'module' });
	const references = [];
	const walk = (node, scopes, parent, key) => {
		if (!node || typeof node.type !== 'string') return;
		let scope = scopes[scopes.length - 1];
		const opensScope =
			/Function|ClassDeclaration|ClassExpression|BlockStatement|ForStatement|ForInStatement|ForOfStatement|CatchClause|Program|StaticBlock/.test(
				node.type
			);
		if (opensScope) {
			scope = new Set();
			scopes = [...scopes, scope];
		}
		if (node.type === 'Program' || /Function/.test(node.type)) {
			const fnScope = scope;
			if (node.type === 'FunctionDeclaration' && node.id) scopes[scopes.length - 2]?.add(node.id.name);
			if (node.type === 'FunctionExpression' && node.id) fnScope.add(node.id.name);
			for (const param of node.params || []) collectPatternNames(param, fnScope);
			if (node.type === 'Program' || node.type.endsWith('Function')) hoistVars(node.body, fnScope);
			if (node.type !== 'Program') fnScope.add('arguments');
		}
		if (node.type === 'ClassDeclaration' && node.id) scopes[scopes.length - 2].add(node.id.name);
		if (node.type === 'ClassExpression' && node.id) scope.add(node.id.name);
		if (node.type === 'CatchClause') collectPatternNames(node.param, scope);
		if (node.type === 'ImportDeclaration') {
			for (const spec of node.specifiers) scopes[0].add(spec.local.name);
		}
		if (node.type === 'VariableDeclaration') {
			for (const decl of node.declarations) collectPatternNames(decl.id, scopes[scopes.length - 1]);
		}
		if (node.type === 'Identifier' && isReference(parent, key)) {
			references.push({ name: node.name, scopes });
		}
		for (const childKey of Object.keys(node)) {
			const child = node[childKey];
			if (Array.isArray(child)) child.forEach((item) => walk(item, scopes, node, childKey));
			else if (child && typeof child === 'object') walk(child, scopes, node, childKey);
		}
	};
	const hoistVars = (body, scope) => {
		const visit = (node) => {
			if (!node || typeof node.type !== 'string') return;
			if (/Function|Class/.test(node.type)) return;
			if (node.type === 'VariableDeclaration' && node.kind === 'var') {
				for (const decl of node.declarations) collectPatternNames(decl.id, scope);
			}
			for (const childKey of Object.keys(node)) {
				const child = node[childKey];
				if (Array.isArray(child)) child.forEach(visit);
				else if (child && typeof child === 'object') visit(child);
			}
		};
		body.forEach(visit);
	};
	walk(ast, [], null, null);
	const missing = new Set();
	for (const ref of references) {
		if (ref.scopes.some((scope) => scope.has(ref.name))) continue;
		if (KNOWN_GLOBALS.has(ref.name)) continue;
		missing.add(ref.name);
	}
	return [...missing].sort();
}

function isReference(parent, key) {
	if (!parent) return true;
	if (parent.type === 'MemberExpression' && key === 'property') return parent.computed;
	if ((parent.type === 'Property' || parent.type === 'MethodDefinition' || parent.type === 'PropertyDefinition') && key === 'key') {
		return parent.computed;
	}
	if (parent.type === 'LabeledStatement' || parent.type === 'BreakStatement' || parent.type === 'ContinueStatement') return false;
	if (parent.type === 'VariableDeclarator' && key === 'id') return false;
	if (/Function/.test(parent.type) && (key === 'id' || key === 'params')) return false;
	if (/Class/.test(parent.type) && key === 'id') return false;
	if (parent.type === 'CatchClause' && key === 'param') return false;
	if (/^Import/.test(parent.type) || /^Export/.test(parent.type) && key === 'exported') return false;
	if (parent.type === 'MetaProperty') return false;
	if (['ObjectPattern', 'ArrayPattern', 'RestElement'].includes(parent.type)) return false;
	if (parent.type === 'AssignmentPattern' && key === 'left') return false;
	if (parent.type === 'Property' && key === 'value' && parent.shorthand === false) return true;
	return true;
}

function componentFiles() {
	const files = readdirSync(srcDir).filter((name) => name.endsWith('.svelte'));
	const nested = readdirSync(join(srcDir, 'components'))
		.filter((name) => name.endsWith('.svelte'))
		.map((name) => join('components', name));
	return [...files, ...nested];
}

test('freeIdentifiers reports a name that is used but never imported or declared', () => {
	assert.deepEqual(freeIdentifiers("import { a } from './a.js'; const x = a(b);"), ['b']);
});

for (const file of componentFiles()) {
	test(`${file} references only imported or declared names`, () => {
		const source = readFileSync(join(srcDir, file), 'utf8');
		const { js } = compile(source, { filename: file, generate: 'client', runes: true, css: 'injected' });
		assert.deepEqual(freeIdentifiers(js.code), []);
	});
}
