<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { brokenDefaultNotice, modeName, offerOf, setupName, sourceOf, type SetupChoice } from '$lib/tool-tabs/image/generate/setup';

	let { taskId, label, phone = false }: { taskId: string; label: string; phone?: boolean } = $props();

	const generate = getGenerateController()!;
	const ui = generate.ui;
	const uid = $props.id();

	let task = $derived($ui.tasks.find((entry) => entry.id === taskId) ?? null);
	let choice = $derived($ui.choice[taskId] ?? null);
	let offer = $derived(task && choice ? offerOf(task.offers, choice) : null);
	let isDefault = $derived.by(() => {
		void $ui;
		return generate.isDefault(taskId);
	});
	let broken = $derived(!!task?.default && !task.default.health.ok);

	function nameOf(target: SetupChoice, offers: NonNullable<typeof task>['offers']): string {
		void $ui;
		const found = offerOf(offers, target);
		if (!found) return '';
		const source = sourceOf(target.setup, generate.choicesOf(found));
		const session = source.kind === 'defaults' ? 'No saved session' : setupName(source);
		return [found.preset_name, modeName(found), session].join(' · ');
	}

	let currentName = $derived(choice && task ? nameOf(choice, task.offers) : '');
	let rememberedName = $derived.by(() => {
		const remembered = task?.default;
		if (!task || !remembered?.health.ok) return '';
		return nameOf(remembered, task.offers);
	});

	let status = $derived<'disabled' | 'on' | 'broken' | 'off'>(!choice || !offer ? 'disabled' : isDefault ? 'on' : broken ? 'broken' : 'off');

	let text = $derived(
		status === 'disabled'
			? 'Choose a preset and a mode first'
			: status === 'on'
				? `Your default for ${label}`
				: status === 'broken'
					? `Replace your default for ${label}`
					: `Use as my default for ${label}`
	);
	let detail = $derived(
		status === 'on'
			? 'Click to stop using it as your default'
			: status === 'broken' && task?.default
				? brokenDefaultNotice(task.default.health, label).replace(/ Pick another\.$/, '')
				: status === 'off'
					? rememberedName
						? `Your default is ${rememberedName}`
						: `New ${label} tools start with ${currentName}`
					: ''
	);

	function toggle() {
		if (status === 'disabled') return;
		void generate.setAsDefault(taskId, status !== 'on');
	}
</script>

<span class="default-star flex flex-none items-center" data-default-star={status}>
	<Tooltip {text} {detail} position={phone ? 'top' : 'bottom'} wrapperClass="inline-flex">
		<span class="inline-flex">
			<button
				type="button"
				class="relative inline-flex items-center justify-center rounded transition-[color,background-color] duration-100 touch-manipulation hover:bg-surface-3/50 disabled:cursor-not-allowed disabled:opacity-50 {phone ? 'h-10 w-10' : 'h-6 w-6'} {status === 'on' ? 'text-signal' : 'text-fg-muted hover:text-fg'}"
				aria-label={status === 'disabled' ? `Use as my default for ${label}` : text}
				aria-pressed={status === 'on'}
				aria-disabled={status === 'disabled' ? 'true' : undefined}
				aria-describedby={status === 'disabled' ? `${uid}-hint` : undefined}
				disabled={status === 'disabled'}
				data-testid="default-star"
				onclick={toggle}
			>
				<Icon name="star" className="h-4 w-4 {status === 'on' ? 'fill-current' : ''}" />
				{#if status === 'broken'}<span class="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-warning" data-default-star-warning></span>{/if}
			</button>
			{#if status === 'disabled'}<span id="{uid}-hint" class="sr-only">{text}</span>{/if}
		</span>
	</Tooltip>
</span>
