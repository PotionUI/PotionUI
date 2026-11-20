import { describe, expect, it, vi } from 'vitest';
import { areaIsMarked, landThenSend } from './sendLanding';
import { sendTargetsFor } from './sendTargets';

describe('areaIsMarked', () => {
	it('while marking, follows what is drawn, saved or not', () => {
		expect(areaIsMarked({ marking: true, drawn: true, savedUsable: false })).toBe(true);
		expect(areaIsMarked({ marking: true, drawn: false, savedUsable: true })).toBe(false);
	});

	it('otherwise follows the saved area that still lines up', () => {
		expect(areaIsMarked({ marking: false, drawn: false, savedUsable: true })).toBe(true);
		expect(areaIsMarked({ marking: false, drawn: false, savedUsable: false })).toBe(false);
	});
});

describe('landThenSend', () => {
	it('lands the marked area first and then sends', async () => {
		const order: string[] = [];
		const done = await landThenSend(
			async () => (order.push('land'), true),
			async () => void order.push('send'),
			() => order.push('failed')
		);
		expect(done).toBe(true);
		expect(order).toEqual(['land', 'send']);
	});

	it('tells the member and sends nothing when landing fails', async () => {
		const send = vi.fn(async () => undefined);
		const failed = vi.fn();
		expect(await landThenSend(async () => false, send, failed)).toBe(false);
		expect(send).not.toHaveBeenCalled();
		expect(failed).toHaveBeenCalledTimes(1);
	});
});

describe('the MASK badge', () => {
	const tabs = [{ id: 't1', name: 'Gen', selectedPreset: 'p' }] as never;
	const fields = { t1: [{ name: 'source', title: 'Source', allowInpaint: true }] } as never;

	it('shows only when an area is marked', () => {
		expect(sendTargetsFor(tabs, fields, {}, true)[0].badge).toBe('MASK');
		expect(sendTargetsFor(tabs, fields, {}, false)[0].badge).toBeUndefined();
	});
});
