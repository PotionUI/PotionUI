<script lang="ts">
	import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
	import { isSubmitEnter } from '$lib/components/modals/confirmKeyboard';
	import { Input } from '$lib/components/ui';
	import type { SaveOutcome } from '$lib/tool-tabs/image/generate/controller';
	import { MAX_NAME_LENGTH, NAME_REQUIRED_MESSAGE } from '$lib/tool-tabs/image/generate/tweaks';

	let {
		label,
		onsave,
		onclose
	}: {
		label: string;
		onsave: (name: string) => Promise<SaveOutcome>;
		onclose: () => void;
	} = $props();

	const inputId = `save-setup-${Math.random().toString(36).slice(2, 10)}`;

	let name = $state('');
	let busy = $state(false);
	let error = $state<string | null>(null);
	let dialog: ReturnType<typeof ConfirmDialog> | undefined = $state();

	async function save(): Promise<boolean> {
		if (!name.trim()) {
			error = NAME_REQUIRED_MESSAGE;
			return false;
		}
		busy = true;
		error = null;
		try {
			const outcome = await onsave(name);
			if (outcome.ok) {
				onclose();
				return true;
			}
			error = outcome.message;
			return false;
		} finally {
			busy = false;
		}
	}

	function keydown(event: KeyboardEvent) {
		if (!isSubmitEnter(event)) return;
		event.preventDefault();
		dialog?.confirm();
	}
</script>

<ConfirmDialog
	bind:this={dialog}
	isOpen={true}
	size="sm"
	title="Save as new setup"
	subtitle="Name it to pick it again for {label}."
	{busy}
	confirmLabel="Save"
	confirmDisabled={!name.trim()}
	onConfirm={save}
	onCancel={onclose}
>
	<div class="px-6 py-5" data-save-setup-dialog>
		<label class="mb-2 block text-sm text-fg-muted" for={inputId}>Setup name</label>
		<Input
			id={inputId}
			bind:value={name}
			invalid={!!error}
			class="text-sm"
			autocomplete="off"
			maxlength={MAX_NAME_LENGTH + 20}
			data-autofocus
			data-save-setup-name
			oninput={() => (error = null)}
			onkeydown={keydown}
		/>
		{#if error}
			<p class="mt-2 text-sm text-danger" role="alert" data-save-setup-error>{error}</p>
		{/if}
	</div>
</ConfirmDialog>
