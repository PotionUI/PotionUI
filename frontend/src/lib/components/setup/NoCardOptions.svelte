<script lang="ts">
	import { Card } from '$lib/components/ui';

	const OPTIONS = [
		{
			id: 'computer',
			title: 'Another computer with an NVIDIA card',
			body: 'Install PotionUI there as a helper and this one sends it the work.',
			needs: 'A second computer with an NVIDIA graphics card.',
			label: 'Admin → Backends',
			href: '/admin?tab=backends',
			howTo: '/docs?doc=remote-native'
		},
		{
			id: 'comfyui',
			title: 'A ComfyUI server',
			body: 'Use a ComfyUI you already run, on this network or elsewhere.',
			needs: 'A ComfyUI server that is running.',
			label: 'Admin → Backends',
			href: '/admin?tab=backends',
			howTo: '/docs?doc=user/comfyui-presets'
		},
		{
			id: 'online',
			title: 'An online service',
			body: 'Make pictures with a paid service instead of your own card.',
			needs: 'A key from the service you choose.',
			label: 'Admin → Plugins',
			href: '/admin?tab=plugins',
			howTo: '/docs?doc=cloud-models'
		}
	];
</script>

<Card padding="none" class="overflow-hidden">
	{#each OPTIONS as item, i (item.id)}
		<div
			class="grid grid-cols-[1fr_auto] items-center gap-3 px-4 py-3 max-sm:grid-cols-1 {i > 0
				? 'border-t border-line'
				: ''}"
			data-no-card-option={item.id}
		>
			<div>
				<div class="font-medium text-fg">{item.title}</div>
				<div class="text-fg-muted">{item.body}</div>
				<div class="text-fg-subtle">You need: {item.needs}</div>
			</div>
			<div class="flex items-center gap-4 max-sm:flex-wrap">
				<a class="font-medium text-signal hover:underline" href={item.href}>{item.label}</a>
				<a class="text-fg-muted hover:text-fg hover:underline" href={item.howTo}>How to</a>
			</div>
		</div>
	{/each}
</Card>
