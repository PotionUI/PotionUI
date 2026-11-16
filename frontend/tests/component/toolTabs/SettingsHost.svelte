<script lang="ts">
	import { untrack } from 'svelte';
	import EditorEntryComponent from '$lib/components/tool-tabs/frame/EditorEntryComponent.svelte';
	import EditStack from '$lib/components/tool-tabs/frame/EditStack.svelte';
	import FloatingPrompt from '$lib/components/tool-tabs/generate/FloatingPrompt.svelte';
	import { railTools } from '$lib/editors/railTools';
	import { entryKey } from '$lib/editors/registry';
	import { createStack } from '$lib/tool-tabs/frame/editStack';
	import { provideGenerateController } from '$lib/tool-tabs/image/generate/context';
	import type { GenerateController } from '$lib/tool-tabs/image/generate/controller';
	import { settingsOf } from '$lib/tool-tabs/image/generate/register';

	let { controller, phone = false }: { controller: GenerateController; phone?: boolean } = $props();
	provideGenerateController(untrack(() => controller));

	$effect(() => controller.use('edit_region'));

	const tool = railTools.get('image', 'edit_region');
	const ref = settingsOf(tool);
	let open = $state(false);
	let settings = $derived({ open, toggle: () => (open = !open) });
	const host = {} as never;
</script>

<div class="flex" data-host>
	<div data-dock>
		{#if phone && open && ref}
			<EditorEntryComponent entry={entryKey('image', 'edit_region')} componentRef={ref} props={{ host, phone, onback: () => (open = false) }} />
		{:else}
			<FloatingPrompt {phone} {settings} />
		{/if}
	</div>
	{#if !phone}
		<aside data-frame-panel>
			{#if open && ref}
				<EditorEntryComponent entry={entryKey('image', 'edit_region')} componentRef={ref} props={{ host, phone, onback: () => (open = false) }} />
			{:else}
				<section data-options-panel>Options</section>
			{/if}
			<EditStack stack={createStack()} original={{ summary: 'harbor-dawn' }} folded={open} onreopen={() => undefined} onseek={() => undefined} ontogglehidden={() => undefined} onremove={() => undefined} onmove={() => undefined} />
		</aside>
	{/if}
</div>
