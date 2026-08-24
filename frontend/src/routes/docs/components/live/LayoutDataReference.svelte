<script lang="ts">
	import { Badge } from '$lib/components/ui';
	import { ChartCard, StatTile } from '$lib/components/charts';
	import MasterDetailLayout from '$lib/components/master-detail/MasterDetailLayout.svelte';
	import { Pane, PaneRow } from '$lib/components/pane';
	import ComponentExample from './ComponentExample.svelte';

	const generationRows = [
		{ day: 'Mon', count: 18 },
		{ day: 'Tue', count: 31 },
		{ day: 'Wed', count: 24 },
		{ day: 'Thu', count: 42 },
		{ day: 'Fri', count: 35 }
	];

	const presets = [
		{ id: 'z-image', title: 'Z-Image Turbo', subtitle: 'Native · txt2img', status: 'ready' },
		{ id: 'sdxl', title: 'SDXL Realistic', subtitle: 'Native · txt2img / img2img', status: 'ready' },
		{ id: 'video', title: 'LTX Video', subtitle: 'ComfyUI · txt2video', status: 'offline' }
	];

	let selectedPreset = 'z-image';
</script>

<div class="space-y-8">
	<ComponentExample
		title="StatTile"
		description="Dense operational metrics with a larger hero option."
		code={`<StatTile label="Generations" value="1,284" hint="+18% this week" hero />`}
	>
		<div class="grid w-full grid-cols-2 gap-3 lg:grid-cols-4">
			<StatTile label="Generations" value="1,284" hint="+18% this week" hero class="col-span-2" />
			<StatTile label="Queue" value="3" hint="2 running" />
			<StatTile label="VRAM" value="18.4 GB" hint="76% utilized" />
		</div>
	</ComponentExample>

	<ComponentExample
		title="ChartCard"
		description="Chart frame with an accessible exact-value table. Use the top-right control to switch views."
		code={`<ChartCard title="Generations" {tableData} {tableColumns}>\n  <!-- chart visualization -->\n</ChartCard>`}
	>
		<div class="w-full max-w-2xl">
			<ChartCard
				title="Generations this week"
				subtitle="Completed outputs by day"
				tableData={generationRows}
				tableColumns={[
					{ key: 'day', label: 'Day' },
					{ key: 'count', label: 'Generations', align: 'right' }
				]}
			>
				<div class="flex h-40 items-end gap-3 border-b border-line px-2 pt-4">
					{#each generationRows as row}
						<div class="flex flex-1 flex-col items-center gap-2">
							<div class="w-full rounded-t bg-signal/70" style={`height: ${(row.count / 42) * 120}px`}></div>
							<span class="font-mono text-2xs text-fg-subtle">{row.day}</span>
						</div>
					{/each}
				</div>
			</ChartCard>
		</div>
	</ComponentExample>

	<ComponentExample
		title="Master-detail layout and Pane"
		description="Resizable on desktop and automatically stacked on narrow devices. Selection is keyboard accessible."
		code={`<MasterDetailLayout leftWidth={240}>\n  <div slot="list">\n    <Pane label="Presets">\n      {#snippet children()}\n        <PaneRow selected title subtitle trailing />\n      {/snippet}\n    </Pane>\n  </div>\n  <div slot="detail">...</div>\n</MasterDetailLayout>`}
	>
		<div class="w-full h-[28rem] overflow-hidden rounded-lg border border-line">
			<MasterDetailLayout leftWidth={240} minWidth={180} maxWidth={320} storageKey="frontend-kit-master-detail">
				<div slot="list" class="h-full">
					<Pane label="Presets">
						{#snippet children()}
							{#each presets as preset}
								{#snippet presetTrailing()}
									<Badge variant={preset.status === 'ready' ? 'success' : 'danger'} size="sm">{preset.status}</Badge>
								{/snippet}
								<PaneRow
									selected={selectedPreset === preset.id}
									onclick={() => (selectedPreset = preset.id)}
									title={preset.title}
									subtitle={preset.subtitle}
									trailing={presetTrailing}
								/>
							{/each}
						{/snippet}
					</Pane>
				</div>
				<div slot="detail" class="h-full p-4 sm:p-6">
					<p class="label">Selected preset</p>
					<h3 class="text-lg font-semibold text-fg">{presets.find((preset) => preset.id === selectedPreset)?.title}</h3>
					<p class="text-sm text-fg-muted mt-2 max-w-lg">This pane is where the selected item’s editable details, metadata, or preview belong.</p>
				</div>
			</MasterDetailLayout>
		</div>
	</ComponentExample>
</div>
