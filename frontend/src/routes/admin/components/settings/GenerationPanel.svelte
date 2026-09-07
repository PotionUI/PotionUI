<script lang="ts">
	import { DetailSection } from '$lib/components/detail';

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
			<input
				type="checkbox"
				id="workbench-single-result-gallery"
				class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
				checked={settings.workbench_single_result_gallery || false}
				onchange={(e) => onSettingChange('workbench_single_result_gallery', e.currentTarget.checked)}
			/>
		</div>
	</div>
</DetailSection>
