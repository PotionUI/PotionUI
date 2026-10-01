import { describe, expect, it } from 'vitest';
import { baseName, defaultExportStem, exportFileName, sanitizeFileStem } from './exportName';

describe('export names', () => {
	it('strips the extension', () => {
		expect(baseName('photo.final.png')).toBe('photo.final');
		expect(baseName('noext')).toBe('noext');
		expect(baseName('.hidden')).toBe('.hidden');
	});

	it('removes characters a file name cannot carry', () => {
		expect(sanitizeFileStem('a/b\\c:d*e?f"g<h>i|j')).toBe('a-b-c-d-e-f-g-h-i-j');
		expect(sanitizeFileStem('  ..dots  and   spaces  ')).toBe('dots and spaces');
	});

	it('caps the stem at 80 characters', () => {
		expect(sanitizeFileStem('x'.repeat(200))).toHaveLength(80);
	});

	it('names an edit after its source and a drawing after nothing', () => {
		expect(defaultExportStem('sample.png')).toBe('sample-edit');
		expect(defaultExportStem(null)).toBe('drawing');
	});

	it('always ends with a single .png and never comes out empty', () => {
		expect(exportFileName('my art')).toBe('my art.png');
		expect(exportFileName('my art.PNG')).toBe('my art.png');
		expect(exportFileName('   ')).toBe('edit.png');
		expect(exportFileName('///', 'draw')).toBe('---.png');
	});
});
