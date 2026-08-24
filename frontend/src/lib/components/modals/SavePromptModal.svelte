<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { api } from '$lib/services/api';
	import type { PromptUsageHint, Segment } from '$lib/types/segments';
	import { flattenRichSegments, toRichSegment } from '$lib/utils/richSegments';
	import { toasts } from '$lib/stores/toast';
	import BaseModal from './BaseModal.svelte';
	import { Button } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';

	export let isOpen = false;
	export let segments: Segment[] = [];
	export let usageHint: PromptUsageHint = 'positive';
	const dispatch = createEventDispatcher<{ close: void; saved: void }>();
	let name = '';
	let saving = false;
	let previousOpen = false;
	$: if (isOpen !== previousOpen) { previousOpen = isOpen; if (isOpen) name = ''; }
	$: preview = flattenRichSegments(segments);

	async function save() {
		saving = true;
		try {
			const response = await api.createPrompt({
				name: name.trim() || null,
				usage_hint: usageHint,
				segments: segments.map(toRichSegment)
			});
			if (!response.success) throw new Error(response.error || 'Failed to save Prompt');
			toasts.success('New detached Prompt saved');
			dispatch('saved');
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to save Prompt');
		} finally {
			saving = false;
		}
	}
</script>

<BaseModal {isOpen} title="Save as Prompt" sizeClass="md:max-w-lg md:w-full" on:close={() => dispatch('close')}>
	<svelte:fragment slot="headerIcon"><Icon name="save" className="h-5 w-5 text-fg-muted" /></svelte:fragment>
	<div class="space-y-4 p-4 sm:p-6">
		<div><label class="mb-1.5 block text-sm font-medium text-fg" for="saved-prompt-name">Name <span class="font-normal text-fg-subtle">(optional)</span></label><input id="saved-prompt-name" class="input w-full" bind:value={name} placeholder="Content preview is used when unnamed" /></div>
		<div><span class="mb-1.5 block text-sm font-medium text-fg">Composition</span><div class="max-h-36 overflow-y-auto rounded-lg border border-line bg-surface-2 p-3 text-sm text-fg-muted">{preview || 'Blank prompt'} </div><p class="mt-1.5 text-xs text-fg-subtle">{segments.length} segment{segments.length === 1 ? '' : 's'} · {usageHint} usage hint</p></div>
		<p class="text-xs text-fg-subtle">Only this segment list is saved. Preset, mode, form values, session, backend, seed, tags, and generation settings are not included.</p>
	</div>
	<svelte:fragment slot="footer"><div class="flex justify-end gap-2 px-4 py-3 sm:px-6"><Button variant="secondary" onclick={() => dispatch('close')}>Cancel</Button><Button variant="primary" loading={saving} onclick={save}>Save Prompt</Button></div></svelte:fragment>
</BaseModal>
