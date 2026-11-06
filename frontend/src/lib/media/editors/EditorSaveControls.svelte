<script lang="ts">
	import { getContext } from 'svelte';
	import { readable } from 'svelte/store';
	import ToolFooter from '$lib/media-tools/ToolFooter.svelte';
	import { EDITOR_RUN_CONTEXT_KEY, type EditorRunContext } from './editorRunContext';
	import type { MediaEditorSaveMode } from './types';

	export let busy: boolean = false;
	export let blockedReason: string | null = null;
	export let failureMessage: string | null = null;
	export let allowReplace: boolean = true;
	export let applyLabel: string = 'Apply';
	export let note: string | null = null;
	export let reserveMessage: boolean = false;
	export let onCancel: () => void;
	export let onSave: (mode: MediaEditorSaveMode) => void;

	const run = getContext<EditorRunContext | undefined>(EDITOR_RUN_CONTEXT_KEY);
	const progress = run?.progress ?? readable<number | null>(null);
	const cancelling = run?.cancelling ?? readable(false);

	let mode: MediaEditorSaveMode = 'new';

	$: running = busy && $progress !== null;
	$: if (!allowReplace) mode = 'new';
</script>

<ToolFooter
	flush
	{mode}
	showModes={allowReplace}
	{note}
	{applyLabel}
	applyIcon={applyLabel === 'Apply'}
	disabled={busy || blockedReason !== null}
	working={busy}
	cancellable={running}
	progress={$progress ?? 0}
	statusLabel={null}
	cancelling={$cancelling}
	failure={failureMessage}
	blocked={blockedReason}
	{reserveMessage}
	onMode={(next) => (mode = next)}
	{onCancel}
	onApply={() => onSave(allowReplace ? mode : 'new')}
	onCancelRun={() => run?.cancel()}
/>
