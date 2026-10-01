<script lang="ts">
	import { Button, Spinner } from '$lib/components/ui';
	import type { MaskDecision } from './maskPolicy';

	export let stem: string;
	export let meta: string;
	export let sourceName: string | null;
	export let decision: MaskDecision;
	export let busy: boolean = false;
	export let failureMessage: string | null = null;
	export let onSave: () => void;
	export let onBack: () => void;

	let input: HTMLInputElement;

	export function focusName() {
		input?.focus();
		input?.select();
	}

	function submit(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			event.preventDefault();
			onSave();
		}
	}
</script>

<div
	role="group"
	aria-label="Save as new upload"
	class="absolute bottom-full right-3 mb-2 z-10 w-[24rem] max-w-[calc(100vw-1.5rem)] rounded-xl border border-line-strong bg-surface-1 shadow-overlay p-3.5 flex flex-col gap-3"
>
	<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Save to Library</p>

	<div class="flex flex-col gap-1">
		<label for="paint-save-name" class="text-xs text-fg-muted">File name</label>
		<div class="flex items-center gap-1.5">
			<input
				id="paint-save-name"
				bind:this={input}
				bind:value={stem}
				autocomplete="off"
				class="input flex-1 min-w-0"
				on:keydown={submit}
			/>
			<span class="font-mono text-xs text-fg-subtle">.png</span>
		</div>
	</div>

	<span
		class="self-start inline-flex px-1.5 py-0.5 rounded bg-surface-2 ring-1 ring-inset ring-line font-mono text-xs tabular-nums text-fg-muted"
	>
		{meta}
	</span>

	{#if sourceName}
		<p class="text-xs text-fg-subtle">
			Added as a new upload derived from <span class="font-semibold text-fg-muted">{sourceName}</span>. The
			original stays as it is.
		</p>
	{:else}
		<p class="text-xs text-fg-subtle">Added as a new upload in your Library.</p>
	{/if}

	{#if decision.notice}
		<p
			class="text-xs rounded border px-2.5 py-2 {decision.fate === 'clear'
				? 'border-warning/40 text-warning'
				: 'border-line-strong text-fg-muted'}"
		>
			{decision.notice}
		</p>
	{/if}

	{#if failureMessage}
		<p class="text-xs text-danger">{failureMessage}</p>
	{/if}

	<div class="flex items-center justify-end gap-2">
		{#if busy}
			<span class="mr-auto inline-flex items-center gap-2 text-xs text-fg-muted">
				<Spinner size="sm" />
				Uploading
			</span>
		{/if}
		<Button variant="ghost" size="sm" onclick={onBack} disabled={busy}>Back</Button>
		<Button variant="primary" size="sm" icon="check" onclick={onSave} disabled={busy || !stem.trim()}>
			Save and use
		</Button>
	</div>
</div>
