import { describe, it, expect } from 'vitest';
import { render } from 'svelte/server';
import PluginSetupPanel from './PluginSetupPanel.svelte';
import type { PluginSetupReport } from '$lib/plugins/setup';

const html = (report: PluginSetupReport, busyStepId: string | null = null) =>
	render(PluginSetupPanel, { props: { report, busyStepId, onAction: () => {} } }).body;

const incomplete: PluginSetupReport = {
	plugin_id: 'fake-provider',
	complete: false,
	remaining: 3,
	steps: [
		{
			id: 'restart',
			kind: 'restart',
			label: 'Restart PotionUI',
			description: 'Restart so the backend this plugin adds can be set up.',
			status: 'todo',
			action: { kind: 'restart', label: 'Restart now' }
		},
		{
			id: '0-backend.added',
			kind: 'backend.added',
			label: 'Add a backend with your credentials',
			description: 'Available after the restart.',
			status: 'waiting'
		},
		{
			id: '1-presets.installed',
			kind: 'presets.installed',
			label: "Install the plugin's presets",
			description: 'None of its 2 presets are installed yet.',
			status: 'todo',
			action: { kind: 'link', label: 'Open presets', href: '/admin?tab=presets&id=p1' }
		}
	],
	guide: { title: 'Fake', href: '/admin?tab=docs&doc=plugin%2Ffake-provider%2FREADME' }
};

describe('PluginSetupPanel', () => {
	it('leads with the next step and its action', () => {
		const out = html(incomplete);
		expect(out).toContain('Next: Restart PotionUI');
		expect(out).toContain('Restart now');
		expect(out).toContain('0 of 3 done');
		expect(out).not.toContain('Setup complete');
	});

	it('lists every step with its status and links later steps to their page', () => {
		const out = html(incomplete);
		expect(out).toContain('Add a backend with your credentials');
		expect(out).toContain('Available after the restart.');
		expect(out).toContain('data-status="waiting"');
		expect(out).toContain('href="/admin?tab=presets&amp;id=p1"');
		expect(out).toContain('Open presets');
	});

	it('links to the plugin guide', () => {
		const out = html(incomplete);
		expect(out).toContain('Read the Fake guide');
		expect(out).toContain('href="/admin?tab=docs&amp;doc=plugin%2Ffake-provider%2FREADME"');
	});

	it('disables every action while one is running', () => {
		const withSettings: PluginSetupReport = {
			...incomplete,
			steps: [
				...incomplete.steps,
				{
					id: '2-plugin.settings',
					kind: 'plugin.settings',
					label: 'Fill in the plugin settings',
					description: 'Still empty: Token.',
					status: 'todo',
					action: { kind: 'settings', label: 'Open settings' }
				}
			]
		};
		const disabledButtons = (out: string) => (out.match(/<button[^>]*\sdisabled(=""|>|\s)/g) ?? []).length;
		expect(disabledButtons(html(withSettings))).toBe(0);
		expect(disabledButtons(html(withSettings, 'restart'))).toBe(2);
	});

	it('says setup is complete and offers no actions once every step is done', () => {
		const out = html({
			plugin_id: 'fake-provider',
			complete: true,
			remaining: 0,
			steps: [
				{
					id: '0-presets.assigned',
					kind: 'presets.assigned',
					label: 'Give people access',
					description: '1 of 1 installed presets are assigned.',
					status: 'done'
				}
			],
			guide: null
		});
		expect(out).toContain('Setup complete');
		expect(out).not.toContain('Next:');
		expect(out).not.toContain('<button');
		expect(out).not.toContain('guide');
	});
});
