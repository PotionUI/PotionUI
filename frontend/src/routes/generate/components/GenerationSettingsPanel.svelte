<script lang="ts">
	import TagSelector from '$lib/components/TagSelector.svelte';
	import AutoCollectionSelector from '$lib/components/AutoCollectionSelector.svelte';
	import PluginSlot from '$lib/components/plugins/PluginSlot.svelte';
	import { Switch } from '$lib/components/ui';
	import { tabsStore } from '$lib/stores/tabs';
	import {
		getApplySoundToAllTabs,
		setApplySoundToAllTabs,
		setGlobalSoundDefault,
		type SoundKind
	} from '$lib/utils/soundSettings';

	// App-level (preset-independent) generation options, rendered inside the
	// generation panel's settings drawer. Plugins extend this panel via the
	// `generate.settings` frontend hook. The panel-layout (2/3 panes) switch
	// lives in the tabs-row overflow menu now — it is saved per session.
	export let tabId: string;
	export let presetId: string | undefined = undefined;
	export let mode: string | undefined = undefined;
	export let autoTagIds: string[] = [];
	export let autoCollectionIds: string[] = [];
	export let soundOnComplete = true;
	export let soundOnError = true;

	let applyToAllTabs = getApplySoundToAllTabs();

	function handleSoundToggle(kind: SoundKind, value: boolean) {
		if (kind === 'complete') {
			soundOnComplete = value;
		} else {
			soundOnError = value;
		}
		const field = kind === 'complete' ? 'soundOnComplete' : 'soundOnError';
		if (applyToAllTabs) {
			setGlobalSoundDefault(kind, value);
			tabsStore.updateAllTabs({ [field]: value });
		} else {
			tabsStore.updateTab(tabId, { [field]: value });
		}
	}

	function handleApplyToAllToggle(value: boolean) {
		applyToAllTabs = value;
		setApplySoundToAllTabs(value);
	}
</script>

<div class="p-4 space-y-5">
	<section>
		<h3 class="label">Auto-tags</h3>
		<p class="text-xs text-fg-subtle mb-2">Applied to every output generated in this tab.</p>
		<TagSelector
			selectedTagIds={autoTagIds}
			on:change={(event) => (autoTagIds = event.detail)}
			triggerStyle="pills"
			listStyle="checklist"
			allowCreate={false}
			loadOnOpen={true}
			placeholder="Auto-tags..."
		/>
	</section>

	<section>
		<h3 class="label">Auto-collections</h3>
		<p class="text-xs text-fg-subtle mb-2">Add every generation from this tab to these collections.</p>
		<AutoCollectionSelector bind:selectedCollectionIds={autoCollectionIds} />
	</section>

	<section>
		<h3 class="label">Sounds</h3>
		<p class="text-xs text-fg-subtle mb-2">Play a sound when this tab's generation finishes.</p>
		<div class="flex items-center justify-between gap-3 py-1.5">
			<span class="text-sm text-fg">On complete</span>
			<Switch
				checked={soundOnComplete}
				onchange={(value) => handleSoundToggle('complete', value)}
				label="Play a sound on generation complete"
				size="sm"
			/>
		</div>
		<div class="flex items-center justify-between gap-3 py-1.5">
			<span class="text-sm text-fg">On error</span>
			<Switch
				checked={soundOnError}
				onchange={(value) => handleSoundToggle('error', value)}
				label="Play a sound on generation error"
				size="sm"
			/>
		</div>
		<div class="flex items-center justify-between gap-3 border-t border-line pt-2 mt-1">
			<span class="text-xs text-fg-subtle">Apply to all tabs</span>
			<Switch
				checked={applyToAllTabs}
				onchange={handleApplyToAllToggle}
				label="Apply sound settings to all tabs"
				size="sm"
			/>
		</div>
	</section>

	<!-- Plugin-contributed settings sections -->
	<PluginSlot hookName="generate.settings" context={{ tabId, presetId, mode }} />
</div>
