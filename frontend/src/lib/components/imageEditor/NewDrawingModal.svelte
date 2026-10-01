<script lang="ts">
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmFooter from '$lib/components/modals/ConfirmFooter.svelte';
	import {
		createConfirmSettlementGate,
		getConfirmKeyboardAction,
		settleIfEligible
	} from '$lib/components/modals/confirmKeyboard';
	import { SegmentedControl } from '$lib/components/ui';
	import { clampSide } from './geometry';
	import { drawSizeChoices, penForBackground, type DrawBackground, type DrawConfig } from './drawConfig';

	export let isOpen: boolean = false;
	export let presetSize: [number, number] | null = null;
	export let defaultBackground: DrawBackground = 'white';
	export let defaultPen: string | null = null;
	export let onCreate: (config: DrawConfig) => void;
	export let onClose: () => void;

	let width = presetSize?.[0] ?? 1024;
	let height = presetSize?.[1] ?? 1024;
	let background: DrawBackground = defaultBackground;
	let start: 'scribble' | 'plain' = 'scribble';

	const settlementGate = createConfirmSettlementGate();
	$: if (isOpen) settlementGate.reset();

	$: choices = drawSizeChoices(presetSize);

	function handleCancel() {
		settleIfEligible(settlementGate, true, onClose);
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, true, create);
	}

	function handleKeydown(event: KeyboardEvent) {
		if (!isOpen) return;
		const { action, suppress } = getConfirmKeyboardAction(event);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) event.preventDefault();
	}

	function create() {
		onCreate({
			width: clampSide(width),
			height: clampSide(height),
			background,
			pen: start === 'scribble' ? penForBackground(background, defaultPen) : defaultPen,
			scribble: start === 'scribble'
		});
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal {isOpen} title="New drawing" size="sm" handleEscapeKey={false} on:close={handleCancel}>
	<div class="flex flex-col gap-4 p-4">
		<div class="flex flex-col gap-2">
			<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Size</p>
			<div class="flex flex-wrap gap-1.5">
				{#each choices as choice (choice.label)}
					<button
						type="button"
						aria-pressed={width === choice.width && height === choice.height}
						class="h-8 px-3 rounded border font-mono text-xs tabular-nums transition-colors {width === choice.width && height === choice.height
							? 'border-signal/60 bg-signal/10 text-signal'
							: 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg'}"
						on:click={() => {
							width = choice.width;
							height = choice.height;
						}}
					>
						{choice.label}
					</button>
				{/each}
			</div>
			<div class="flex items-center gap-2">
				<input
					type="number"
					min="16"
					max="4096"
					bind:value={width}
					aria-label="Width"
					class="input flex-1 min-w-0 font-mono tabular-nums"
				/>
				<span class="text-fg-subtle">×</span>
				<input
					type="number"
					min="16"
					max="4096"
					bind:value={height}
					aria-label="Height"
					class="input flex-1 min-w-0 font-mono tabular-nums"
				/>
			</div>
		</div>

		<div class="flex flex-col gap-2">
			<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Background</p>
			<SegmentedControl
				variant="toggle"
				ariaLabel="Background"
				items={[
					{ id: 'white', label: 'White' },
					{ id: 'black', label: 'Black' },
					{ id: 'transparent', label: 'Transparent' }
				]}
				selected={background}
				onSelect={(id) => (background = id as DrawBackground)}
			/>
		</div>

		<div class="flex flex-col gap-2">
			<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Start with</p>
			<SegmentedControl
				variant="toggle"
				ariaLabel="Start with"
				items={[
					{ id: 'scribble', label: 'Scribble pen' },
					{ id: 'plain', label: 'Plain brush' }
				]}
				selected={start}
				onSelect={(id) => (start = id as 'scribble' | 'plain')}
			/>
			<p class="text-xs leading-relaxed text-fg-subtle">
				Scribble pen draws 6 px lines in a colour that contrasts with the background, which is what a
				Scribble guide reads.
			</p>
		</div>
	</div>

	<svelte:fragment slot="footer">
		<ConfirmFooter confirmLabel="Start drawing" onCancel={handleCancel} onConfirm={handleConfirm} />
	</svelte:fragment>
</BaseModal>
