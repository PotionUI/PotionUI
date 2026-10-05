// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import type { FieldIndex } from '../../src/lib/formulas/fieldIndex';

const { default: FormulaApplyView } = await import('../../src/lib/components/formulas/FormulaApplyView.svelte');
const { createClassComponent } = await import('svelte/legacy');

const index: FieldIndex = new Map([
	[
		'resolution',
		{
			name: 'resolution',
			label: 'Resolution',
			type: 'resolution',
			options: [{ value: '1344x768' }, { value: '1024x1024' }] as never,
			advanced: false,
			controllers: [],
			node: {}
		}
	]
]);

describe('formula apply preview, resolution row', () => {
	it('shows the real old and new dimensions', () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = createClassComponent({
			component: FormulaApplyView as never,
			target,
			props: {
				formula: { id: 'f', name: 'Wide', groups: [{ id: 'size', label: 'Size', fields: ['resolution'] }] },
				plan: {
					changes: [
						{
							field: 'resolution',
							label: 'Resolution',
							group: 'size',
							groupLabel: 'Size',
							old: '1024x1024',
							new: '1344x768',
							advanced: false,
							type: 'resolution'
						}
					],
					same: [],
					skips: []
				},
				loading: false,
				error: null,
				index,
				groupOrder: ['size'],
				presetName: 'Krea-2',
				modeLabel: 'Text to image',
				loraMode: 'replace',
				selected: new Set(['resolution']),
				ready: true,
				onToggle: () => {},
				onLoraMode: () => {},
				onApply: () => {},
				onCancel: () => {}
			}
		});
		const row = target.querySelector('[data-change-field="resolution"]')!;
		const text = row.textContent ?? '';
		expect(text).toContain('1024 × 1024');
		expect(text).toContain('1344 × 768');
		component.$destroy();
		target.remove();
	});
});
