<script lang="ts">
	import { DetailSection } from '$lib/components/detail';
	import { Input, Switch } from '$lib/components/ui';
	import ApiKeysAccessSection from '../apikeys/ApiKeysAccessSection.svelte';
	import McpCeilingSection from '../apikeys/McpCeilingSection.svelte';
	import PersonAccessSection from '../apikeys/PersonAccessSection.svelte';
	import type { CeilingDraft } from '../apikeys/ceilingDraft.svelte';

	let {
		settings,
		savedSnapshot,
		ceiling,
		onSettingChange
	}: {
		settings: Record<string, any>;
		savedSnapshot: string;
		ceiling: CeilingDraft;
		onSettingChange: (key: string, value: unknown) => void;
	} = $props();
</script>

<DetailSection label="Access" padded={false}>
	<div class="px-4 sm:px-5 divide-y divide-line">
		<div class="py-4 flex items-start justify-between gap-6">
			<div>
				<label for="allow-registration" class="block text-sm font-medium text-fg mb-1">
					Allow anyone to register
				</label>
				<p class="text-sm text-fg-muted">
					When off, only the owner can create accounts in Administration → Users.
				</p>
			</div>
			<Switch
				id="allow-registration"
				label="Allow anyone to register"
				checked={settings.registration_policy === 'open'}
				onchange={(checked) => onSettingChange('registration_policy', checked ? 'open' : 'closed')}
			/>
		</div>

		<div class="py-4">
			<label for="public-base-url" class="block text-sm font-medium text-fg mb-1">Public address</label>
			<p class="text-sm text-fg-muted mb-2">
				Used to build links to generations for external clients such as MCP. Leave empty to use the address the client connected to.
			</p>
			<Input
				id="public-base-url"
				value={settings.public_base_url ?? ''}
				oninput={(e: Event) => onSettingChange('public_base_url', (e.currentTarget as HTMLInputElement).value)}
				placeholder="https://potion.example.com"
			/>
		</div>
	</div>
</DetailSection>

<ApiKeysAccessSection {settings} {savedSnapshot} {onSettingChange} />
<McpCeilingSection {ceiling} />
<PersonAccessSection />
