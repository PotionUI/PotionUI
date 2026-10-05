import { describe, it, expect } from 'vitest';
import { render } from 'svelte/server';
import PluginCard from './PluginCard.svelte';
import type { Plugin } from '$lib/stores/plugins';
import type { PluginSetupReport } from '$lib/plugins/setup';

const plugin = {
	id: 'fake-provider',
	name: 'Fake Provider',
	version: '1.0.0',
	type: 'backend-only',
	enabled: true,
	manifest_path: '/plugins/fake-provider/manifest.yml',
	description: 'Cloud models.',
	category: 'backends'
} as Plugin;

const pending: PluginSetupReport = { plugin_id: 'fake-provider', complete: false, remaining: 2, steps: [] };

const html = (props: Record<string, unknown>) =>
	render(PluginCard, { props: { plugin, onOpen: () => {}, onToggle: () => {}, ...props } as any }).body;

describe('PluginCard setup badge', () => {
	it('shows while setup is incomplete', () => {
		expect(html({ setup: pending })).toContain('Setup needed');
	});

	it('is absent without a pending setup', () => {
		expect(html({})).not.toContain('Setup needed');
		expect(html({ setup: null })).not.toContain('Setup needed');
	});

	it('gives way to the error badge', () => {
		expect(html({ setup: pending, plugin: { ...plugin, state: 'error', error: 'bad' } })).not.toContain('Setup needed');
	});
});
