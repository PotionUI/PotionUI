import { describe, it, expect } from 'vitest';
import { buildExamples, changeableFields, KEY_PLACEHOLDER, type ScriptTemplate } from './scriptExamples';

function template(overrides: Partial<ScriptTemplate['session']> = {}): ScriptTemplate {
	return {
		session: {
			id: '01JC0SESSION',
			name: 'Portrait',
			preset_id: 'p',
			preset_name: 'P',
			modes: ['txt2img'],
			mode: 'txt2img',
			settings_hash: 'abc123',
			...overrides
		},
		prompt: { positive: 'a lighthouse', negative: '', batch: 1 },
		shortcuts: { seed: 'seed', count: 'quantity' },
		fields: [
			{ name: 'steps', label: 'Steps', type: 'slider', kind: 'value', value: 30, locked: false, media: false },
			{ name: 'note', label: 'Note', type: 'alert', kind: 'display', locked: false, media: false }
		],
		problems: [],
		editor_only: null
	};
}

const options = { base: 'https://host', pinned: false };

describe('buildExamples', () => {
	it('fills the session id and url and reads the key from the environment', () => {
		const examples = buildExamples(template(), options);

		expect(examples.curl).toContain('https://host/api/sessions/01JC0SESSION/generate');
		expect(examples.curl).toContain(`Bearer ${KEY_PLACEHOLDER}`);
		expect(examples.python).toContain('os.environ["POTIONUI_KEY"]');
		expect(examples.javascript).toContain('process.env.POTIONUI_KEY');
	});

	it('never contains a secret-shaped value', () => {
		for (const text of Object.values(buildExamples(template(), options))) expect(text).not.toMatch(/pui_[A-Za-z0-9]/);
	});

	it('names the mode only when the session holds several', () => {
		expect(buildExamples(template(), options).curl).not.toContain('"mode"');
		const many = buildExamples(template({ modes: ['txt2img', 'img2img'], mode: 'img2img' }), options);
		expect(many.curl).toContain('"mode":"img2img"');
		expect(many.mcp).toContain('"mode": "img2img"');
	});

	it('adds the settings hash only when pinned', () => {
		expect(buildExamples(template(), options).curl).not.toContain('if_settings_hash');
		const pinned = buildExamples(template(), { ...options, pinned: true });
		expect(pinned.curl).toContain('"if_settings_hash":"abc123"');
		expect(pinned.python).toContain('"if_settings_hash": "abc123"');
	});

	it('builds a start_generation tool call with the session id', () => {
		const call = JSON.parse(buildExamples(template(), options).mcp);
		expect(call.params.name).toBe('start_generation');
		expect(call.params.arguments.session_id).toBe('01JC0SESSION');
	});
});

describe('changeableFields', () => {
	it('leaves out display-only fields', () => {
		expect(changeableFields(template()).map((f) => f.name)).toEqual(['steps']);
	});
});
