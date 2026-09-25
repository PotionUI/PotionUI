import { describe, it, expect } from 'vitest';
import { applyArtifactValues, textArtifactAction, textArtifactValues } from './textArtifact';

const ABC = 'X:1\nK:D\n"D"d2 f2 a2 |]\n';

describe('textArtifactValues', () => {
	it('writes the text into the action field alongside the action values', () => {
		const values = textArtifactValues({
			title: 'ABC transcription · full',
			text: ABC,
			action: { label: 'Use as ABC', field: 'abc', values: { cot: 'full' } }
		});
		expect(values).toEqual({ abc: ABC, cot: 'full' });
	});

	it('lets the text win over a same-named action value', () => {
		const values = textArtifactValues({
			title: 't',
			text: ABC,
			action: { label: 'Use', field: 'abc', values: { abc: 'stale' } }
		});
		expect(values).toEqual({ abc: ABC });
	});

	it('returns null without an action or a field', () => {
		expect(textArtifactValues({ title: 't', text: ABC })).toBeNull();
		expect(textArtifactValues({ title: 't', text: ABC, action: null })).toBeNull();
		expect(
			textArtifactAction({ title: 't', text: ABC, action: { label: 'x', field: '' } })
		).toBeNull();
	});
});

describe('applyArtifactValues', () => {
	it('overwrites only the applied keys and keeps the rest of the form', () => {
		const next = applyArtifactValues(
			{ abc: '', cot: 'off', style: 'folk', seed: 7 },
			{ abc: ABC, cot: 'melody' }
		);
		expect(next).toEqual({ abc: ABC, cot: 'melody', style: 'folk', seed: 7 });
	});

	it('does not mutate the input form data', () => {
		const form = { cot: 'off' };
		applyArtifactValues(form, { cot: 'full' });
		expect(form).toEqual({ cot: 'off' });
	});
});
