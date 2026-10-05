import { describe, it, expect } from 'vitest';
import {
	addLimit,
	byteUnitOptions,
	valueToDisplay,
	availableKinds,
	bytesToDisplay,
	displayToValue,
	draftFromPlan,
	draftToBody,
	emptyDraft,
	isDraftDirty,
	isDraftValid,
	removeLimit,
	setLimitText,
	setLimitUnit
} from './editor';
import type { LimitKindDescriptor, Plan } from './types';

const GB = 1024 ** 3;
const TB = 1024 ** 4;

const kinds: LimitKindDescriptor[] = [
	{ key: 'storage_bytes', label: 'Storage space', description: '', value_type: 'bytes', unit: 'GB', input_scale: 1073741824, window: 'none' },
	{ key: 'generations_per_day', label: 'Generations per day', description: '', value_type: 'count', unit: 'per day', input_scale: 1, window: 'day' },
	{ key: 'cloud_spend_usd_month', label: 'Cloud spend per month', description: '', value_type: 'usd', unit: 'USD / month', input_scale: 1, window: 'month' },
	{ key: 'credits', label: 'Credits', description: '', value_type: 'count', unit: 'credits', input_scale: 1, window: 'none', plugin: true }
];

describe('value conversion', () => {
	it('converts GB and TB text to bytes', () => {
		expect(displayToValue(kinds[0], '20', 'GB')).toBe(20 * GB);
		expect(displayToValue(kinds[0], '1.5', 'TB')).toBe(1.5 * TB);
	});

	it('keeps counts integral and rounds currency to cents', () => {
		expect(displayToValue(kinds[1], '100', 'GB')).toBe(100);
		expect(displayToValue(kinds[1], '1.5', 'GB')).toBeNull();
		expect(displayToValue(kinds[2], '50.456', 'GB')).toBe(50.46);
	});

	it('rejects blank, negative and non numeric input', () => {
		expect(displayToValue(kinds[0], '', 'GB')).toBeNull();
		expect(displayToValue(kinds[0], '-1', 'GB')).toBeNull();
		expect(displayToValue(kinds[0], 'abc', 'GB')).toBeNull();
	});

	it('shows whole terabytes as TB and everything else as GB', () => {
		expect(bytesToDisplay(2 * TB)).toEqual({ text: '2', unit: 'TB' });
		expect(bytesToDisplay(1.5 * TB)).toEqual({ text: '1536', unit: 'GB' });
		expect(bytesToDisplay(5 * GB)).toEqual({ text: '5', unit: 'GB' });
	});
});

describe('draft editing', () => {
	it('adds a limit once and offers only unused kinds', () => {
		let draft = emptyDraft();
		draft = addLimit(draft, kinds[0]);
		draft = addLimit(draft, kinds[0]);
		expect(draft.limits).toHaveLength(1);
		expect(availableKinds(kinds, draft).map((k) => k.key)).toEqual(['generations_per_day', 'cloud_spend_usd_month', 'credits']);
	});

	it('keeps a limit of an inactive kind untouched and sends it back unchanged', () => {
		const plan: Plan = {
			id: 'p',
			name: 'Old',
			description: '',
			is_system: false,
			limits: [
				{ kind: 'storage_bytes', value: 5 * GB },
				{ kind: 'gone.credits', value: 500, active: false }
			]
		};
		const draft = draftFromPlan(plan, kinds);
		expect(draft.limits[1].inactiveValue).toBe(500);
		expect(draftToBody(draft, kinds)?.limits).toEqual(plan.limits.map((l) => ({ kind: l.kind, value: l.value })));
	});

	it('converts a scaled count kind with input_scale', () => {
		const thousands = { ...kinds[1], input_scale: 1000 };
		expect(displayToValue(thousands, '2', 'GB')).toBe(2000);
	});

	it('removes a limit and makes the kind available again', () => {
		const draft = removeLimit(addLimit(emptyDraft(), kinds[1]), 'generations_per_day');
		expect(draft.limits).toEqual([]);
		expect(availableKinds(kinds, draft)).toHaveLength(4);
	});
});

describe('save body', () => {
	it('builds name, description and converted limits', () => {
		let draft = { ...emptyDraft(), name: ' Tier 1 ', description: 'First tier' };
		draft = setLimitText(addLimit(draft, kinds[0]), 'storage_bytes', '20');
		draft = setLimitText(addLimit(draft, kinds[1]), 'generations_per_day', '100');
		expect(draftToBody(draft, kinds)).toEqual({
			name: 'Tier 1',
			description: 'First tier',
			limits: [
				{ kind: 'storage_bytes', value: 20 * GB },
				{ kind: 'generations_per_day', value: 100 }
			]
		});
	});

	it('uses the chosen byte unit', () => {
		let draft = setLimitText(addLimit({ ...emptyDraft(), name: 'Big' }, kinds[0]), 'storage_bytes', '2');
		draft = setLimitUnit(draft, 'storage_bytes', 'TB');
		expect(draftToBody(draft, kinds)?.limits[0].value).toBe(2 * TB);
	});

	it('returns null when a value is blank and marks the draft invalid', () => {
		const draft = addLimit({ ...emptyDraft(), name: 'X' }, kinds[0]);
		expect(draftToBody(draft, kinds)).toBeNull();
		expect(isDraftValid(draft, kinds)).toBe(false);
	});

	it('requires a name', () => {
		expect(isDraftValid(emptyDraft(), kinds)).toBe(false);
	});
});

describe('round trip', () => {
	const plan: Plan = {
		id: 'p1',
		name: 'Tier 2',
		description: '',
		is_system: false,
		limits: [
			{ kind: 'storage_bytes', value: 100 * GB },
			{ kind: 'cloud_spend_usd_month', value: 50 }
		]
	};

	it('loads a plan and serialises back to the same limits', () => {
		const draft = draftFromPlan(plan, kinds);
		expect(draft.limits[0]).toEqual({ kind: 'storage_bytes', text: '100', unit: 'GB' });
		expect(draftToBody(draft, kinds)?.limits).toEqual(plan.limits);
	});

	it('tracks dirtiness against a snapshot', () => {
		const snapshot = draftFromPlan(plan, kinds);
		expect(isDraftDirty(snapshot, snapshot)).toBe(false);
		expect(isDraftDirty(removeLimit(snapshot, 'storage_bytes'), snapshot)).toBe(true);
	});
});

describe('per-file upload size kind', () => {
	const MB = 1024 ** 2;
	const upload: LimitKindDescriptor = {
		key: 'upload_file_size',
		label: 'Largest upload',
		description: '',
		value_type: 'bytes',
		unit: 'MB',
		input_scale: MB,
		window: 'none',
		per_item: true
	};

	it('offers MB and GB for it and GB and TB for storage', () => {
		expect(byteUnitOptions(upload).map((o) => o.value)).toEqual(['MB', 'GB']);
		expect(byteUnitOptions(kinds[0]).map((o) => o.value)).toEqual(['GB', 'TB']);
	});

	it('round-trips 50 MB and 1 GB', () => {
		expect(valueToDisplay(upload, 52428800)).toEqual({ text: '50', unit: 'MB' });
		expect(valueToDisplay(upload, GB)).toEqual({ text: '1', unit: 'GB' });
		expect(displayToValue(upload, '50', 'MB')).toBe(52428800);
		expect(displayToValue(upload, '1', 'GB')).toBe(GB);
	});

	it('starts a new limit in MB and saves the typed value', () => {
		const draft = setLimitText(addLimit(emptyDraft(), upload), 'upload_file_size', '50');
		expect(draft.limits[0].unit).toBe('MB');
		expect(draftToBody({ ...draft, name: 'Free' }, [upload])?.limits).toEqual([{ kind: 'upload_file_size', value: 52428800 }]);
	});
});
