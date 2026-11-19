import { describe, expect, it } from 'vitest';
import { hintsOf, readOptions, runOptionsOf, viaNote, withOption } from './taskOptions';

describe('task options', () => {
	it('offers the shadow hint on Remove only', () => {
		expect(hintsOf('remove_object').map((hint) => hint.name)).toEqual(['include_shadow']);
		expect(hintsOf('edit_region')).toEqual([]);
		expect(hintsOf(null)).toEqual([]);
	});

	it('adds an option when on and drops it when off', () => {
		const on = withOption(undefined, 'include_shadow', true);
		expect(on).toEqual({ include_shadow: true });
		expect(withOption(on, 'include_shadow', false)).toEqual({});
	});

	it('sends nothing when every option is off', () => {
		expect(runOptionsOf({})).toBeUndefined();
		expect(runOptionsOf({ include_shadow: false })).toBeUndefined();
		expect(runOptionsOf({ include_shadow: true })).toEqual({ include_shadow: true });
	});

	it('reads stored options and ignores junk', () => {
		expect(readOptions({ include_shadow: true })).toEqual({ include_shadow: true });
		expect(readOptions(null)).toBeUndefined();
		expect(readOptions([true])).toBeUndefined();
		expect(readOptions('x')).toBeUndefined();
	});
});

describe('viaNote', () => {
	it('names the fallback for an offer that reaches the task through it', () => {
		expect(viaNote({ via: 'edit_region' })).toBe('Uses Edit a region with a built-in instruction.');
	});

	it('says nothing for a direct offer', () => {
		expect(viaNote({ via: null })).toBeNull();
		expect(viaNote(null)).toBeNull();
	});

	it('uses the registered task label when it knows one', () => {
		expect(viaNote({ via: 'acme.fill' }, (id) => (id === 'acme.fill' ? 'Fill' : null))).toBe('Uses Fill with a built-in instruction.');
		expect(viaNote({ via: 'acme.fill' })).toBeNull();
	});
});
