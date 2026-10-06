import type { Cube } from './types';

export const MIN_CUBE_SIZE = 2;
export const MAX_CUBE_SIZE = 65;

export class CubeError extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'CubeError';
	}
}

type Triple = [number, number, number];

const NON_FINITE = /^[+-]?(nan|inf|infinity)$/i;

function triple(lineNo: number, keyword: string, parts: string[]): Triple {
	if (parts.length !== 3) throw new CubeError(`line ${lineNo}: ${keyword} needs three numbers`);
	if (parts.some((part) => NON_FINITE.test(part))) {
		throw new CubeError(`line ${lineNo}: ${keyword} must be finite`);
	}
	const values = parts.map(Number);
	if (values.some((v) => Number.isNaN(v))) {
		throw new CubeError(`line ${lineNo}: ${keyword} needs three numbers`);
	}
	if (!values.every(Number.isFinite)) {
		throw new CubeError(`line ${lineNo}: ${keyword} must be finite`);
	}
	return values as Triple;
}

export function parseCube(text: string): Cube {
	let size = 0;
	let title = '';
	let domainMin: Triple = [0, 0, 0];
	let domainMax: Triple = [1, 1, 1];
	const rows: number[] = [];
	const lines = text.split(/\r\n|\r|\n/);
	for (let index = 0; index < lines.length; index++) {
		const lineNo = index + 1;
		const line = lines[index].split('#', 1)[0].trim();
		if (!line) continue;
		const parts = line.split(/\s+/);
		const keyword = parts[0].toUpperCase();
		if (keyword === 'TITLE') {
			title = line.slice(parts[0].length).trim().replace(/^"|"$/g, '');
		} else if (keyword === 'LUT_3D_SIZE') {
			if (parts.length !== 2 || !/^\d+$/.test(parts[1])) {
				throw new CubeError(`line ${lineNo}: LUT_3D_SIZE needs one whole number`);
			}
			size = parseInt(parts[1], 10);
			if (size < MIN_CUBE_SIZE || size > MAX_CUBE_SIZE) {
				throw new CubeError(
					`line ${lineNo}: LUT_3D_SIZE ${size} is outside ${MIN_CUBE_SIZE}..${MAX_CUBE_SIZE}`
				);
			}
		} else if (keyword === 'LUT_1D_SIZE') {
			throw new CubeError(`line ${lineNo}: 1D LUTs are not supported (LUT_1D_SIZE)`);
		} else if (keyword === 'LUT_3D_INPUT_RANGE') {
			throw new CubeError(`line ${lineNo}: LUT_3D_INPUT_RANGE is not supported`);
		} else if (keyword === 'DOMAIN_MIN') {
			domainMin = triple(lineNo, 'DOMAIN_MIN', parts.slice(1));
		} else if (keyword === 'DOMAIN_MAX') {
			domainMax = triple(lineNo, 'DOMAIN_MAX', parts.slice(1));
		} else if (/^[A-Za-z]/.test(keyword)) {
			throw new CubeError(`line ${lineNo}: unknown keyword '${parts[0]}'`);
		} else {
			if (!size) throw new CubeError(`line ${lineNo}: data before LUT_3D_SIZE`);
			if (parts.length !== 3) throw new CubeError(`line ${lineNo}: a data row needs three numbers`);
			if (parts.some((part) => NON_FINITE.test(part))) {
				throw new CubeError(`line ${lineNo}: data values must be finite`);
			}
			const row = parts.map(Number);
			if (row.some((v) => Number.isNaN(v))) {
				throw new CubeError(`line ${lineNo}: a data row needs three numbers`);
			}
			if (!row.every(Number.isFinite)) {
				throw new CubeError(`line ${lineNo}: data values must be finite`);
			}
			rows.push(row[0], row[1], row[2]);
		}
	}
	if (!size) throw new CubeError('missing LUT_3D_SIZE');
	const expected = size ** 3;
	if (rows.length / 3 !== expected) {
		throw new CubeError(
			`expected ${expected} data rows for LUT_3D_SIZE ${size}, found ${rows.length / 3}`
		);
	}
	if (domainMin.some((lo, i) => domainMax[i] <= lo)) {
		throw new CubeError('DOMAIN_MAX must be greater than DOMAIN_MIN on every channel');
	}
	return { size, data: Float32Array.from(rows), domainMin, domainMax, title };
}
