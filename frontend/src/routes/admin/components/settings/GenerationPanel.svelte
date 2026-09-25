<script lang="ts">
	import { DetailSection } from '$lib/components/detail';
	import { Switch } from '$lib/components/ui';

	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();
</script>

<DetailSection label="Generation" padded={false}>
	<div class="px-4 sm:px-5 divide-y divide-line">
		<div class="py-4 flex items-start justify-between gap-6">
			<div>
				<label for="workbench-single-result-gallery" class="block text-sm font-medium text-fg mb-1">
					Show gallery for single-output runs
				</label>
				<p class="text-sm text-fg-muted">
					When off, a run that produced one image or video fills the workbench without a gallery
					strip below it.
				</p>
			</div>
			<Switch
				id="workbench-single-result-gallery"
				label="Show gallery for single-output runs"
				checked={settings.workbench_single_result_gallery || false}
				onchange={(checked) => onSettingChange('workbench_single_result_gallery', checked)}
			/>
		</div>
	</div>
</DetailSection>
