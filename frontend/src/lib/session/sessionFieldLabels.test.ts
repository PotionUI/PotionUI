import { describe, it, expect } from 'vitest';
import { fieldLabelsFromSchema } from './sessionFieldLabels';

describe('fieldLabelsFromSchema', () => {
	it('maps nested field names to their titles and skips untitled ones', () => {
		const schema = {
			properties: {
				root: {
					children: [
						{ type: 'text', name: 'diffusion_model', title: 'Diffusion model' },
						{ type: 'group', children: [{ type: 'number', name: 'cfg', title: ' CFG scale ' }] },
						{ type: 'number', name: 'untitled' }
					]
				}
			}
		};
		expect(fieldLabelsFromSchema(schema)).toEqual({ diffusion_model: 'Diffusion model', cfg: 'CFG scale' });
	});

	it('returns nothing for a missing schema', () => {
		expect(fieldLabelsFromSchema(null)).toEqual({});
	});
});
