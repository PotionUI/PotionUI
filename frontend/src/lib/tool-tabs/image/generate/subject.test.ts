import { describe, expect, it } from 'vitest';
import type { EditorTaskInfo, TaskOffer } from '$lib/services/api/editorTasks';
import { backgroundMarkFrom, subjectChoice } from './subject';

const offer = (over: Partial<TaskOffer> = {}): TaskOffer => ({ preset_id: 'matting', preset_name: 'Matting', mode: 'editor:matte', from_mode: null, mode_label: 'Matte', label: null, short_description: null, order: 0, region_strategy: 'whole', via: null, qualifies: true, reason: null, ...over });
const task = (over: Partial<EditorTaskInfo> = {}): Pick<EditorTaskInfo, 'offers' | 'default'> => ({ offers: [offer()], default: null, ...over });

describe('subjectChoice', () => {
	it('uses the first model that qualifies, on its defaults, without asking', () => {
		const choice = subjectChoice(task({ offers: [offer({ qualifies: false, preset_id: 'a' }), offer({ preset_id: 'b' }), offer({ preset_id: 'c' })] }));
		expect(choice).toEqual({ preset_id: 'b', mode: 'editor:matte', setup: { kind: 'defaults' } });
	});

	it('prefers a healthy default and ignores a broken one', () => {
		const healthy = { preset_id: 'other', mode: 'editor:m2', setup: { kind: 'session' as const, id: 's1' }, health: { ok: true, reason: null, name: null, mode_label: null } };
		expect(subjectChoice(task({ default: healthy }))).toEqual({ preset_id: 'other', mode: 'editor:m2', setup: { kind: 'session', id: 's1' } });
		const broken = { ...healthy, health: { ok: false, reason: 'preset_missing' as const, name: null, mode_label: null } };
		expect(subjectChoice(task({ default: broken }))?.preset_id).toBe('matting');
	});

	it('finds nothing when no matting model is offered', () => {
		expect(subjectChoice(task({ offers: [] }))).toBeNull();
		expect(subjectChoice(task({ offers: [offer({ qualifies: false })] }))).toBeNull();
		expect(subjectChoice(null)).toBeNull();
	});
});

describe('backgroundMarkFrom', () => {
	it('marks the background: the inverse of the subject alpha, keeping soft values', () => {
		const rgba = new Uint8ClampedArray([10, 10, 10, 255, 10, 10, 10, 0, 10, 10, 10, 64, 10, 10, 10, 200]);
		expect([...backgroundMarkFrom(rgba)]).toEqual([0, 255, 191, 55]);
	});
});
