<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import ColorField from '$lib/components/form-fields/ColorField.svelte';
	import ImageSourcePicker from '$lib/components/tool-tabs/image/ImageSourcePicker.svelte';
	import { Button, SegmentedControl, Spinner } from '$lib/components/ui';
	import type { EditorToolProps } from '$lib/editors/railTools';
	import { BACKGROUND_MODES, directBlockedReason, type BackgroundMode } from '$lib/tool-tabs/image/generate/backgroundStep';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { maskArea } from '$lib/tool-tabs/image/maskOverlays';
	import type { MediaRef } from '$lib/types/tabs';

	let {}: EditorToolProps = $props();

	const generate = getGenerateController()!;
	const ui = generate.ui;

	let picker = $state<ReturnType<typeof ImageSourcePicker>>();

	let background = $derived($ui.background);
	let subject = $derived($ui.subject);
	let finding = $derived(subject.status === 'running');
	let direct = $derived(background.mode !== 'describe');
	let reason = $derived(directBlockedReason(background, !!$maskArea));
	let percent = $derived(Math.round(subject.progress * 100));

	function pickMode(id: string) {
		generate.setBackground({ mode: id as BackgroundMode });
	}

	function pickColor(_name: string, value: unknown) {
		if (typeof value === 'string') generate.setBackground({ color: value });
	}
</script>

<div class="flex flex-col gap-2" data-replace-background-subject>
	<p class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Subject</p>
	<p class="text-sm text-fg-muted">Find the subject to mark everything behind it, then fix the edges with the brush or lasso.</p>
	<div class="flex items-center gap-2.5">
		<Button size="sm" icon="sparkles" disabled={finding} onclick={() => void generate.findSubject()} attrs={{ 'data-find-subject': '' }}>Find the subject</Button>
		{#if finding}
			<span class="inline-flex items-center gap-2 font-mono text-sm tabular-nums text-fg-muted" role="status" data-subject-progress><Spinner size="sm" />{percent > 0 ? `${percent}%` : 'Working'}</span>
		{:else if subject.status === 'done'}
			<span class="inline-flex items-center gap-1.5 text-sm text-success" data-subject-done><Icon name="check" className="h-3.5 w-3.5" />Subject found</span>
		{/if}
	</div>
	{#if subject.status === 'failed' && subject.error}
		<p class="text-sm text-danger" role="alert" data-subject-error>{subject.error}</p>
	{/if}
</div>

<div class="flex flex-col gap-2" data-replace-background-choice>
	<p class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">New background</p>
	<SegmentedControl variant="toggle" fullWidth ariaLabel="New background" items={[...BACKGROUND_MODES]} selected={background.mode} onSelect={pickMode} />
	{#if background.mode === 'describe'}
		<p class="text-sm text-fg-muted" data-background-describe>Describe it in the box below, then press Generate.</p>
	{:else if background.mode === 'picture'}
		<div class="flex items-center gap-2.5" data-background-picture>
			{#if background.picture?.url}
				<img src={background.picture.url} alt="" class="h-10 w-10 flex-none rounded border border-line-strong object-cover" />
			{/if}
			<span class="min-w-0 flex-1 truncate text-sm {background.picture ? 'text-fg' : 'text-fg-muted'}">{background.picture?.name ?? 'No picture chosen'}</span>
			<Button size="sm" icon="image" onclick={() => picker?.open('library')} attrs={{ 'data-background-choose': '' }}>{background.picture ? 'Change' : 'Choose a picture'}</Button>
		</div>
	{:else if background.mode === 'color'}
		<div data-background-color>
			<ColorField name="color" config={{ title: 'Colour', default: background.color }} value={background.color} onChange={pickColor} />
		</div>
	{:else}
		<p class="text-sm text-fg-muted" data-background-transparent>The background is removed. Save as PNG to keep it transparent.</p>
	{/if}
	{#if direct}
		<Button variant="primary" size="sm" class="w-full" disabled={!!reason || $ui.starting} onclick={() => void generate.applyBackground(!!$maskArea)} attrs={{ 'data-replace-background-apply': '' }}>Replace the background</Button>
		{#if reason}
			<p class="text-sm text-fg-muted" data-replace-background-reason>{reason}</p>
		{/if}
	{/if}
</div>

{#if $ui.error && direct}
	<p class="text-sm text-danger" role="alert" data-replace-background-error>{$ui.error}</p>
{/if}

<ImageSourcePicker
	bind:this={picker}
	onpick={(source: MediaRef) => void generate.chooseBackgroundPicture(source)}
	onfile={(file: File) => void generate.chooseBackgroundPicture(file)}
/>
