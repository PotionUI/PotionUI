export type DeriveScalar = number | boolean | string;
export type DeriveValue = DeriveScalar | { readonly [key: string]: DeriveScalar };
export type DeriveEnv = Readonly<Record<string, DeriveValue>>;

export class DeriveError extends Error {
	constructor(
		readonly stage: 'parse' | 'eval',
		message: string
	) {
		super(message);
		this.name = 'DeriveError';
	}
}

type BinOp = '+' | '-' | '*' | '/' | '%' | '**';
type CmpOp = '==' | '!=' | '<' | '<=' | '>' | '>=';

export type DeriveNode =
	| { t: 'lit'; v: DeriveScalar }
	| { t: 'name'; id: string }
	| { t: 'attr'; obj: string; key: string }
	| { t: 'neg' | 'pos' | 'not'; arg: DeriveNode }
	| { t: 'bin'; op: BinOp; l: DeriveNode; r: DeriveNode }
	| { t: 'cmp'; first: DeriveNode; ops: CmpOp[]; rest: DeriveNode[] }
	| { t: 'and' | 'or'; items: DeriveNode[] }
	| { t: 'if'; test: DeriveNode; yes: DeriveNode; no: DeriveNode }
	| { t: 'call'; fn: string; args: DeriveNode[] };

type Token = { k: 'num'; v: number } | { k: 'str'; v: string } | { k: 'name'; v: string } | { k: 'op'; v: string } | { k: 'end' };

const KEYWORDS = new Set([
	'None', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue', 'def', 'del', 'elif', 'except',
	'finally', 'for', 'from', 'global', 'import', 'in', 'is', 'lambda', 'nonlocal', 'pass', 'raise', 'return',
	'try', 'while', 'with', 'yield'
]);
const FUNCTIONS = new Set(['min', 'max', 'clamp', 'abs', 'floor', 'round', 'sqrt', 'sin', 'cos', 'pow']);
const OPERATORS = ['**', '==', '!=', '<=', '>=', '<', '>', '+', '-', '*', '/', '%', '(', ')', ',', '.'];
const NUMBER = /(?:0[xX](?:_?[0-9a-fA-F])+|0[oO](?:_?[0-7])+|0[bB](?:_?[01])+|(?:\d(?:_?\d)*(?:\.(?:\d(?:_?\d)*)?)?|\.\d(?:_?\d)*)(?:[eE][+-]?\d(?:_?\d)*)?)/y;
const NAME = /[A-Za-z_][A-Za-z0-9_]*/y;
const ESCAPES: Record<string, string> = { '\\': '\\', "'": "'", '"': '"', n: '\n', t: '\t' };

function parseNumber(text: string): number {
	const plain = text.replace(/_/g, '');
	if (/^0[xX]/.test(plain)) return parseInt(plain.slice(2), 16);
	if (/^0[oO]/.test(plain)) return parseInt(plain.slice(2), 8);
	if (/^0[bB]/.test(plain)) return parseInt(plain.slice(2), 2);
	if (/^0\d/.test(plain) && /^\d+$/.test(plain) && /[1-9]/.test(plain)) throw new DeriveError('parse', 'leading zeros are not allowed');
	return Number(plain);
}

function tokenize(source: string): Token[] {
	const tokens: Token[] = [];
	let i = 0;
	while (i < source.length) {
		const ch = source[i];
		if (ch === ' ' || ch === '\t') {
			i += 1;
			continue;
		}
		NUMBER.lastIndex = i;
		const number = /[0-9.]/.test(ch) ? NUMBER.exec(source) : null;
		if (number && number[0] !== '.' && number[0].length > 0) {
			i += number[0].length;
			if (/[A-Za-z0-9_]/.test(source[i] ?? '')) throw new DeriveError('parse', 'invalid number');
			tokens.push({ k: 'num', v: parseNumber(number[0]) });
			continue;
		}
		if (ch === "'" || ch === '"') {
			let j = i + 1;
			let text = '';
			while (j < source.length && source[j] !== ch) {
				if (source[j] === '\\') {
					const escaped = ESCAPES[source[j + 1]];
					if (escaped === undefined) throw new DeriveError('parse', 'unsupported escape');
					text += escaped;
					j += 2;
				} else {
					text += source[j];
					j += 1;
				}
			}
			if (j >= source.length) throw new DeriveError('parse', 'unterminated string');
			tokens.push({ k: 'str', v: text });
			i = j + 1;
			continue;
		}
		NAME.lastIndex = i;
		const name = NAME.exec(source);
		if (name) {
			i += name[0].length;
			if (source[i] === "'" || source[i] === '"') throw new DeriveError('parse', 'string prefixes are not allowed');
			tokens.push({ k: 'name', v: name[0] });
			continue;
		}
		const op = OPERATORS.find((candidate) => source.startsWith(candidate, i));
		if (!op || source.startsWith('//', i) || source.startsWith('<<', i) || source.startsWith('>>', i) || source.startsWith(':=', i)) {
			throw new DeriveError('parse', `unexpected '${ch}'`);
		}
		tokens.push({ k: 'op', v: op });
		i += op.length;
	}
	tokens.push({ k: 'end' });
	return tokens;
}

class Parser {
	private at = 0;

	constructor(private readonly tokens: Token[]) {}

	private peek(): Token {
		return this.tokens[this.at];
	}

	private isOp(value: string): boolean {
		const token = this.peek();
		return token.k === 'op' && token.v === value;
	}

	private isWord(value: string): boolean {
		const token = this.peek();
		return token.k === 'name' && token.v === value;
	}

	private take(): Token {
		const token = this.tokens[this.at];
		this.at += 1;
		return token;
	}

	private expectOp(value: string) {
		if (!this.isOp(value)) throw new DeriveError('parse', `expected '${value}'`);
		this.take();
	}

	parse(): DeriveNode {
		const node = this.expression();
		if (this.peek().k !== 'end') throw new DeriveError('parse', 'unexpected text after the expression');
		return node;
	}

	private expression(): DeriveNode {
		const yes = this.disjunction();
		if (!this.isWord('if')) return yes;
		this.take();
		const test = this.disjunction();
		if (!this.isWord('else')) throw new DeriveError('parse', "expected 'else'");
		this.take();
		return { t: 'if', test, yes, no: this.expression() };
	}

	private disjunction(): DeriveNode {
		const items = [this.conjunction()];
		while (this.isWord('or')) {
			this.take();
			items.push(this.conjunction());
		}
		return items.length === 1 ? items[0] : { t: 'or', items };
	}

	private conjunction(): DeriveNode {
		const items = [this.inversion()];
		while (this.isWord('and')) {
			this.take();
			items.push(this.inversion());
		}
		return items.length === 1 ? items[0] : { t: 'and', items };
	}

	private inversion(): DeriveNode {
		if (this.isWord('not')) {
			this.take();
			return { t: 'not', arg: this.inversion() };
		}
		return this.comparison();
	}

	private comparison(): DeriveNode {
		const first = this.sum();
		const ops: CmpOp[] = [];
		const rest: DeriveNode[] = [];
		while (['==', '!=', '<', '<=', '>', '>='].some((op) => this.isOp(op))) {
			ops.push((this.take() as { v: CmpOp }).v);
			rest.push(this.sum());
		}
		return ops.length ? { t: 'cmp', first, ops, rest } : first;
	}

	private sum(): DeriveNode {
		let node = this.term();
		while (this.isOp('+') || this.isOp('-')) {
			const op = (this.take() as { v: BinOp }).v;
			node = { t: 'bin', op, l: node, r: this.term() };
		}
		return node;
	}

	private term(): DeriveNode {
		let node = this.factor();
		while (this.isOp('*') || this.isOp('/') || this.isOp('%')) {
			const op = (this.take() as { v: BinOp }).v;
			node = { t: 'bin', op, l: node, r: this.factor() };
		}
		return node;
	}

	private factor(): DeriveNode {
		if (this.isOp('-') || this.isOp('+')) {
			const op = this.take() as { v: string };
			return { t: op.v === '-' ? 'neg' : 'pos', arg: this.factor() };
		}
		return this.power();
	}

	private power(): DeriveNode {
		const base = this.primary();
		if (!this.isOp('**')) return base;
		this.take();
		return { t: 'bin', op: '**', l: base, r: this.factor() };
	}

	private primary(): DeriveNode {
		const token = this.take();
		if (token.k === 'num') return this.noTrailer({ t: 'lit', v: token.v });
		if (token.k === 'str') {
			let text = token.v;
			while (this.peek().k === 'str') text += (this.take() as { v: string }).v;
			return this.noTrailer({ t: 'lit', v: text });
		}
		if (token.k === 'op' && token.v === '(') {
			const inner = this.expression();
			if (this.isOp(',')) throw new DeriveError('parse', 'tuples are not allowed');
			this.expectOp(')');
			return this.noTrailer(inner);
		}
		if (token.k !== 'name') throw new DeriveError('parse', 'expected a value');
		const id = token.v;
		if (KEYWORDS.has(id) || ['if', 'else', 'and', 'or', 'not'].includes(id)) throw new DeriveError('parse', `'${id}' is not allowed here`);
		if (id === 'True' || id === 'False') return this.noTrailer({ t: 'lit', v: id === 'True' });
		if (this.isOp('(')) {
			if (!FUNCTIONS.has(id)) throw new DeriveError('parse', `${id}() is not a known function`);
			this.take();
			const args: DeriveNode[] = [];
			if (!this.isOp(')')) {
				for (;;) {
					if (this.isOp('*') || this.isOp('**')) throw new DeriveError('parse', 'unpacking is not allowed');
					args.push(this.expression());
					if (!this.isOp(',')) break;
					this.take();
					if (this.isOp(')')) break;
				}
			}
			this.expectOp(')');
			return this.noTrailer({ t: 'call', fn: id, args });
		}
		if (this.isOp('.')) {
			this.take();
			const key = this.take();
			if (key.k !== 'name') throw new DeriveError('parse', 'expected an attribute name');
			return this.noTrailer({ t: 'attr', obj: id, key: key.v });
		}
		return { t: 'name', id };
	}

	private noTrailer(node: DeriveNode): DeriveNode {
		if (this.isOp('(') || this.isOp('.')) throw new DeriveError('parse', 'only one level of attributes and direct calls are allowed');
		return node;
	}
}

export function parseDerive(source: string): DeriveNode {
	return new Parser(tokenize(source.trim())).parse();
}

type Runtime = DeriveValue;

function fail(message: string): never {
	throw new DeriveError('eval', message);
}

function isObject(value: Runtime): value is { readonly [key: string]: DeriveScalar } {
	return typeof value === 'object' && value !== null;
}

function numeric(value: Runtime): number {
	if (typeof value === 'boolean') return value ? 1 : 0;
	if (typeof value === 'number') return value;
	return fail('a number was expected');
}

function truthy(value: Runtime): boolean {
	if (typeof value === 'string') return value.length > 0;
	if (isObject(value)) return fail('a value was expected');
	return numeric(value) !== 0;
}

function equal(a: Runtime, b: Runtime): boolean {
	if (isObject(a) || isObject(b)) return a === b;
	if (typeof a === 'string' || typeof b === 'string') return a === b;
	return numeric(a) === numeric(b);
}

function order(a: Runtime, b: Runtime): number {
	if (typeof a === 'string' && typeof b === 'string') return a < b ? -1 : a > b ? 1 : 0;
	if (typeof a === 'string' || typeof b === 'string') return fail('strings and numbers cannot be ordered');
	return Math.sign(numeric(a) - numeric(b));
}

function compare(op: CmpOp, a: Runtime, b: Runtime): boolean {
	if (op === '==') return equal(a, b);
	if (op === '!=') return !equal(a, b);
	const sign = order(a, b);
	if (op === '<') return sign < 0;
	if (op === '<=') return sign <= 0;
	if (op === '>') return sign > 0;
	return sign >= 0;
}

function pyMod(a: number, b: number): number {
	if (b === 0) fail('modulo by zero');
	let mod = a % b;
	if (mod !== 0 && b < 0 !== mod < 0) mod += b;
	return mod;
}

function pyPow(a: number, b: number): number {
	if (a === 0 && b < 0) fail('zero to a negative power');
	if (a < 0 && !Number.isInteger(b)) fail('the result is not a real number');
	const out = a ** b;
	if (!Number.isFinite(out) && Number.isFinite(a) && Number.isFinite(b)) fail('the result is too large');
	return out;
}

function binary(op: BinOp, left: Runtime, right: Runtime): Runtime {
	if (op === '+' && typeof left === 'string' && typeof right === 'string') return left + right;
	const a = numeric(left);
	const b = numeric(right);
	switch (op) {
		case '+':
			return a + b;
		case '-':
			return a - b;
		case '*':
			return a * b;
		case '/':
			if (b === 0) fail('division by zero');
			return a / b;
		case '%':
			return pyMod(a, b);
		case '**':
			return pyPow(a, b);
	}
}

function roundHalfEven(x: number): number {
	const floor = Math.floor(x);
	const diff = x - floor;
	if (diff > 0.5) return floor + 1;
	if (diff < 0.5) return floor;
	return floor % 2 === 0 ? floor : floor + 1;
}

function roundDigits(x: number, digits: number): number {
	if (!Number.isFinite(x)) return x;
	if (digits <= 0) {
		const scale = 10 ** -digits;
		return roundHalfEven(x / scale) * scale;
	}
	const magnitude = Math.abs(x);
	const exact = magnitude.toFixed(Math.min(100, digits + 40));
	const dot = exact.indexOf('.');
	const tail = exact.slice(dot + 1 + digits);
	let text: string;
	if (/^50*$/.test(tail)) {
		const kept = exact.slice(0, dot + 1 + digits);
		const last = Number(kept[kept.length - 1]);
		text = last % 2 === 0 ? kept : (Number(kept) + 10 ** -digits).toFixed(digits);
	} else {
		text = magnitude.toFixed(digits);
	}
	const out = Number(text);
	return x < 0 ? -out : out;
}

function arity(fn: string, args: Runtime[], min: number, max = min) {
	if (args.length < min || args.length > max) fail(`${fn}() takes ${min === max ? min : `${min} to ${max}`} arguments`);
}

function finiteArg(fn: string, value: Runtime): number {
	const out = numeric(value);
	if (!Number.isFinite(out)) fail(`${fn}() needs a finite number`);
	return out;
}

function call(fn: string, args: Runtime[]): Runtime {
	if (args.some(isObject)) fail(`${fn}() takes plain values`);
	switch (fn) {
		case 'min':
		case 'max': {
			if (args.length < 2) fail(`${fn}() needs at least two values`);
			let best = args[0];
			for (const value of args.slice(1)) {
				const sign = order(value, best);
				if (fn === 'min' ? sign < 0 : sign > 0) best = value;
			}
			return best;
		}
		case 'clamp': {
			arity(fn, args, 3);
			const low = call('min', [args[2], args[0]]);
			return call('max', [args[1], low]);
		}
		case 'abs':
			arity(fn, args, 1);
			return Math.abs(numeric(args[0]));
		case 'floor':
			arity(fn, args, 1);
			return Math.floor(finiteArg(fn, args[0]));
		case 'round': {
			arity(fn, args, 1, 2);
			if (args.length === 1) return roundHalfEven(finiteArg(fn, args[0]));
			if (typeof args[1] !== 'number' && typeof args[1] !== 'boolean') fail('round() needs whole digits');
			const digits = numeric(args[1]);
			if (!Number.isInteger(digits)) fail('round() needs whole digits');
			return roundDigits(numeric(args[0]), digits);
		}
		case 'sqrt': {
			arity(fn, args, 1);
			const value = numeric(args[0]);
			if (value < 0) fail('sqrt() of a negative number');
			return Math.sqrt(value);
		}
		case 'sin':
			arity(fn, args, 1);
			return Math.sin(finiteArg(fn, args[0]));
		case 'cos':
			arity(fn, args, 1);
			return Math.cos(finiteArg(fn, args[0]));
		case 'pow': {
			arity(fn, args, 2);
			const a = numeric(args[0]);
			const b = numeric(args[1]);
			if (a === 0 && b < 0) fail('zero to a negative power');
			return pyPow(a, b);
		}
	}
	return fail(`${fn}() is not a known function`);
}

function lookup(env: DeriveEnv, id: string): Runtime {
	if (Object.hasOwn(env, id)) return env[id];
	if (id === 'pi') return Math.PI;
	return fail(`'${id}' is not defined`);
}

function evaluate(node: DeriveNode, env: DeriveEnv): Runtime {
	switch (node.t) {
		case 'lit':
			return node.v;
		case 'name':
			return lookup(env, node.id);
		case 'attr': {
			const base = lookup(env, node.obj);
			if (!isObject(base) || !Object.hasOwn(base, node.key)) return fail(`'${node.obj}.${node.key}' is not defined`);
			return base[node.key];
		}
		case 'not':
			return !truthy(evaluate(node.arg, env));
		case 'neg':
			return -numeric(evaluate(node.arg, env));
		case 'pos':
			return numeric(evaluate(node.arg, env));
		case 'bin':
			return binary(node.op, evaluate(node.l, env), evaluate(node.r, env));
		case 'cmp': {
			let left = evaluate(node.first, env);
			for (let i = 0; i < node.ops.length; i += 1) {
				const right = evaluate(node.rest[i], env);
				if (!compare(node.ops[i], left, right)) return false;
				left = right;
			}
			return true;
		}
		case 'and':
		case 'or': {
			let value: Runtime = false;
			for (const item of node.items) {
				value = evaluate(item, env);
				if (node.t === 'and' ? !truthy(value) : truthy(value)) return value;
			}
			return value;
		}
		case 'if':
			return truthy(evaluate(node.test, env)) ? evaluate(node.yes, env) : evaluate(node.no, env);
		case 'call':
			return call(
				node.fn,
				node.args.map((arg) => evaluate(arg, env))
			);
	}
}

export function evaluateDerive(node: DeriveNode | string, env: DeriveEnv): DeriveScalar {
	const value = evaluate(typeof node === 'string' ? parseDerive(node) : node, env);
	if (isObject(value)) return fail('the result is not a value');
	if (typeof value === 'number' && !Number.isFinite(value)) return fail('the result is not a finite number');
	return value;
}

export function deriveBlock(derive: Readonly<Record<string, string>>, env: DeriveEnv): Record<string, DeriveScalar> {
	const scope: Record<string, DeriveValue> = { ...env };
	const values: Record<string, DeriveScalar> = {};
	for (const [name, source] of Object.entries(derive)) {
		const value = evaluateDerive(source, scope);
		scope[name] = value;
		values[name] = value;
	}
	return values;
}
