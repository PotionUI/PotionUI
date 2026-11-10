<script lang="ts">
	import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/ui';
	import type { PresetMirrorRef } from '$lib/types/api';

	let {
		isOpen = false,
		presetName = '',
		mirrors = [],
		onClose,
		onOpenMirror
	}: {
		isOpen?: boolean;
		presetName?: string;
		mirrors?: PresetMirrorRef[];
		onClose: () => void;
		onOpenMirror: (presetId: string) => void;
	} = $props();

	const titleId = `has-mirrors-title-${Math.random().toString(36).slice(2, 9)}`;
	const count = $derived(mirrors.length);
</script>

<ConfirmDialog
	{isOpen}
	size="md"
	compact
	hideCloseButton
	hideCancel
	dialogRole="alertdialog"
	labelledBy={titleId}
	confirmLabel="Got it"
	onConfirm={onClose}
	onCancel={onClose}
>
	<div class="p-7" data-preset-has-mirrors>
		<div class="flex items-start gap-4">
			<div class="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-full bg-warning/10">
				<Icon name="warning" className="h-5 w-5 text-warning" strokeWidth={1.5} />
			</div>
			<div class="min-w-0 flex-1 pt-0.5">
				<h3 id={titleId} class="mb-1.5 break-words text-base font-semibold text-fg">Remove its mirrors first</h3>
				<p class="text-sm leading-relaxed text-fg-muted">
					“{presetName}” can’t be removed while {count === 1 ? 'this mirror uses' : `these ${count} mirrors use`} its files.
					Remove {count === 1 ? 'it' : 'them'} first, then try again.
				</p>
				{#if count}
					<ul class="mt-3 divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface-2">
						{#each mirrors as mirror (mirror.preset_id)}
							<li class="flex items-center justify-between gap-3 px-3 py-2" data-has-mirrors-item>
								<span class="min-w-0 truncate text-sm text-fg">{mirror.name}</span>
								<Button variant="ghost" size="xs" onclick={() => onOpenMirror(mirror.preset_id)}>Open</Button>
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		</div>
	</div>
</ConfirmDialog>
