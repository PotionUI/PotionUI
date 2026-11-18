<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button } from '$lib/components/ui';
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

<span class="flex flex-none items-center" data-default-button={status}>
	<Tooltip {text} {detail} position={phone ? 'top' : 'bottom'} wrapperClass="flex flex-shrink-0">
		<Button
			variant="secondary"
			size="sm"
			disabled={status === 'disabled'}
			ariaLabel={status === 'disabled' ? `Use as my default for ${label}` : text}
			class="relative {phone ? '!h-10' : '!h-8'} !px-2 !py-0"
			attrs={{
				'aria-pressed': status === 'on',
				'aria-disabled': status === 'disabled' ? 'true' : undefined,
				'aria-describedby': status === 'disabled' ? `${uid}-hint` : undefined,
				'data-testid': 'default-button'
			}}
			onclick={toggle}
		>
			<Icon name="star" className="h-3.5 w-3.5 {status === 'on' ? 'fill-current text-signal' : 'text-fg-muted'}" />
			<span class="default-label">Default</span>
			{#if status === 'broken'}<span class="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-warning" data-default-button-warning></span>{/if}
		</Button>
	</Tooltip>
	{#if status === 'disabled'}<span id="{uid}-hint" class="sr-only">{text}</span>{/if}
</span>
