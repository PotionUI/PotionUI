import { describe, it, expect } from 'vitest';
import {
	addLimit,
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
	{ key: 'storage_bytes', label: 'Storage space', description: '', value_type: 'bytes', unit: 'bytes', window: 'none' },
	{ key: 'generations_per_day', label: 'Generations per day', description: '', value_type: 'count', unit: 'count', window: 'day' },
	{ key: 'cloud_spend_usd_month', label: 'Cloud spend per month', description: '', value_type: 'usd', unit: 'usd', window: 'month' },
	{ key: 'credits', label: 'Credits', description: '', value_type: 'count', unit: 'count', window: 'none', plugin: 'image-gen' }
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

	it('skips inactive plugin kinds in the picker', () => {
		const withInactive = [...kinds, { ...kinds[3], key: 'old', active: false }];
		expect(availableKinds(withInactive, emptyDraft()).some((k) => k.key === 'old')).toBe(false);
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
