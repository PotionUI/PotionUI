<script lang="ts">
	import { resolveAxisEditor } from '$lib/fields/registry';
	import ChipsEditor from './editors/ChipsEditor.svelte';
	import NumberEditor from './editors/NumberEditor.svelte';
	import SeedEditor from './editors/SeedEditor.svelte';
	import ModelEditor from './editors/ModelEditor.svelte';
	import LoraEditor from './editors/LoraEditor.svelte';
	import ResolutionEditor from './editors/ResolutionEditor.svelte';
	import PromptEditor from './editors/PromptEditor.svelte';
	import CheckboxEditor from './editors/CheckboxEditor.svelte';
	import type { AxisCandidate, CompareAxis, CompareAxisValue } from '$lib/generation/compare/types';

	let {
		candidate,
		axis,
		tabId,
		onChange
	}: {
		candidate: AxisCandidate;
		axis: CompareAxis | null;
		tabId: string;
		onChange: (values: CompareAxisValue[]) => void;
	} = $props();

	let pluginEditor = $derived(candidate.editor === 'plugin' ? resolveAxisEditor(candidate.type) : null);
</script>

{#if candidate.editor === 'chips'}
	{#key candidate.field}<ChipsEditor {candidate} {axis} {tabId} {onChange} />{/key}
{:else if candidate.editor === 'number'}
	{#key candidate.field}<NumberEditor {candidate} {axis} {tabId} {onChange} />{/key}
{:else if candidate.editor === 'seed'}
	{#key candidate.field}<SeedEditor {candidate} {axis} {tabId} {onChange} />{/key}
{:else if candidate.editor === 'model'}
	{#key candidate.field}<ModelEditor {candidate} {axis} {tabId} {onChange} />{/key}
{:else if candidate.editor === 'lora'}
	{#key candidate.field}<LoraEditor {candidate} {axis} {tabId} {onChange} />{/key}
{:else if candidate.editor === 'resolution'}
	{#key candidate.field}<ResolutionEditor {candidate} {axis} {tabId} {onChange} />{/key}
{:else if candidate.editor === 'prompt'}
	{#key candidate.field}<PromptEditor {candidate} {axis} {tabId} {onChange} />{/key}
{:else if candidate.editor === 'checkbox'}
	{#key candidate.field}<CheckboxEditor {candidate} {axis} {tabId} {onChange} />{/key}
{:else if candidate.editor === 'plugin' && pluginEditor}
	{#await pluginEditor then Component}
		{#if Component}
			{#key candidate.field}<Component {candidate} {axis} {tabId} {onChange} />{/key}
		{:else}
			<p class="text-xs text-fg-muted" data-testid="not-comparable">Not comparable</p>
		{/if}
	{/await}
{:else}
	<p class="text-xs text-fg-muted" data-testid="not-comparable">Not comparable</p>
{/if}
