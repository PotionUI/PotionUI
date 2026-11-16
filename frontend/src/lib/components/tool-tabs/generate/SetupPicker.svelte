<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Button, EmptyState, IconButton, Switch } from '$lib/components/ui';
	import FrameSheet from '$lib/components/tool-tabs/frame/FrameSheet.svelte';
	import dismiss from '$lib/actions/dismiss';
	import { authStore } from '$lib/stores/auth';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { pickerGroups, sameChoice, type SetupChoice } from '$lib/tool-tabs/image/generate/setup';

	let { phone = false, taskId, label }: { phone?: boolean; taskId: string; label: string } = $props();

	const generate = getGenerateController()!;
	const ui = generate.ui;

	let task = $derived($ui.tasks.find((entry) => entry.id === taskId) ?? null);
	let current = $derived($ui.choice[taskId] ?? null);
	let selected = $state<SetupChoice | null>(null);
	let asDefault = $state(false);
	let groups = $derived(task ? pickerGroups(task.offers, (offer) => generate.choicesOf(offer)) : []);
	let anyUsable = $derived(!!task?.offers.some((offer) => offer.qualifies));
	let none = $derived(!task || task.offers.length === 0);
	let isAdmin = $derived($authStore.user?.account_type === 'ADMIN');
	let chosen = $derived(selected ?? current);
	let notice = $derived($ui.picker.notice);

	$effect(() => {
		if (selected === null && current) selected = current;
	});

	function use() {
		if (!chosen) return;
		void generate.pick(taskId, chosen, asDefault);
	}

	function close() {
		generate.closePicker();
	}
</script>

{#snippet body()}
	<div class="flex h-11 flex-none items-center gap-2 border-b border-line pl-4 pr-2">
		<h3 class="min-w-0 flex-1 truncate text-base font-semibold text-fg">Setup for {label}</h3>
		<IconButton icon="close" label="Close" size="sm" onclick={close} />
	</div>
	{#if notice}
		<div class="flex flex-none items-start gap-2 border-b border-warning/25 bg-warning/10 px-4 py-2.5 text-sm text-warning" role="alert" data-picker-notice>
			<Icon name="warning" className="mt-0.5 h-3.5 w-3.5 flex-none" /><span class="text-fg">{notice}</span>
		</div>
	{/if}
	{#if none}
		<div class="min-h-0 flex-1 overflow-y-auto" data-picker-empty>
			<EmptyState
				compact
				icon="model"
				title="No model can do this yet"
				description={isAdmin ? 'Install a model that edits pictures, for example from Admin → Recipes.' : 'Ask your admin to add a model that edits pictures.'}
			/>
		</div>
	{:else}
		<div class="min-h-0 flex-1 overflow-y-auto px-2 pb-2 pt-1" role="radiogroup" aria-label="Setups" data-picker-list>
			{#each groups as group (group.label)}
				<div class="flex items-center gap-2 px-2 pb-1 pt-3 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">
					{group.label}<span class="h-px flex-1 bg-line"></span>
				</div>
				{#each group.modes as mode (mode.offer.mode + mode.offer.preset_id)}
					<div class="px-2 py-1">
						<b class="block text-base font-semibold leading-[18px] text-fg">{mode.offer.label ?? mode.offer.mode_label}</b>
						<small class="block text-sm text-fg-subtle">{mode.disabledReason ?? mode.how}</small>
					</div>
					{#each mode.options as option (option.key)}
						{@const on = !mode.disabledReason && sameChoice(chosen, option.choice)}
						<button
							type="button"
							role="radio"
							aria-checked={on}
							disabled={!!mode.disabledReason}
							class="my-0.5 flex min-h-9 w-full items-center gap-2.5 rounded border px-2.5 py-1.5 text-left text-base transition-colors disabled:cursor-not-allowed disabled:opacity-55 {on
								? 'border-signal bg-signal/10'
								: 'border-line hover:border-line-hover hover:bg-surface-2'}"
							onclick={() => (selected = option.choice)}
							data-picker-option={option.kind}
						>
							<span class="grid h-3.5 w-3.5 flex-none place-items-center rounded-full border {on ? 'border-signal' : 'border-line-hover'}">
								{#if on}<span class="h-1.5 w-1.5 rounded-full bg-signal"></span>{/if}
							</span>
							{#if option.kind === 'formula'}<Icon name="sliders" className="h-3.5 w-3.5 flex-none text-fg-muted" />{/if}
							<span class="min-w-0 flex-1 truncate">{option.name}</span>
							{#if option.meta}<span class="flex-none text-sm text-fg-subtle">{option.meta}</span>{/if}
						</button>
					{/each}
				{/each}
			{/each}
		</div>
		<div class="flex flex-none items-center gap-3 border-t border-line px-4 py-3">
			<label class="flex min-w-0 flex-1 items-center gap-2.5 text-sm text-fg-muted">
				<Switch size="sm" bind:checked={asDefault} label="Use as my default for {label}" disabled={!anyUsable} />
				<span class="min-w-0 truncate">Use as my default for {label}</span>
			</label>
			<Button variant="primary" size="sm" disabled={!chosen || !anyUsable} onclick={use} attrs={{ 'data-picker-use': '' }}>Use</Button>
		</div>
	{/if}
{/snippet}

{#if phone}
	<FrameSheet label="Setup for {label}" onclose={close}>
		<div class="flex max-h-[70vh] min-h-0 flex-col" data-setup-picker="sheet">{@render body()}</div>
	</FrameSheet>
{:else}
	<div
		class="absolute bottom-[calc(100%+8px)] left-0 z-30 flex max-h-[min(480px,calc(100dvh-240px))] w-[400px] max-w-[calc(100vw-32px)] flex-col overflow-hidden rounded-xl border border-line-strong bg-surface-1 shadow-overlay"
		role="dialog"
		aria-label="Setup for {label}"
		use:dismiss={{ onDismiss: close, outside: true, ignore: [document.querySelector<HTMLElement>('[data-setup-chip]')] }}
		data-setup-picker="popover"
	>
		{@render body()}
	</div>
{/if}
