<script lang="ts">
	import { DetailSection } from '$lib/components/detail';
	import { Switch } from '$lib/components/ui';
	import { FAILURE_ALERT_CATEGORIES, normalizeCategories, toggleCategory } from './failureAlerts';

	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();

	let alertsEnabled = $derived(Boolean(settings.notify_admins_on_generation_failure));
	let alertCategories = $derived(normalizeCategories(settings.notify_admins_on_generation_failure_categories));
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
		<div class="py-4 space-y-4">
			<div class="flex items-start justify-between gap-6">
				<div>
					<label for="notify-admins-generation-failure" class="block text-sm font-medium text-fg mb-1">
						Notify admins about failed generations
					</label>
					<p class="text-sm text-fg-muted">
						Every admin gets a notification when any user's generation fails, linking to the
						failure in Admin → Generations.
					</p>
				</div>
				<Switch
					id="notify-admins-generation-failure"
					label="Notify admins about failed generations"
					checked={alertsEnabled}
					onchange={(checked) => onSettingChange('notify_admins_on_generation_failure', checked)}
				/>
			</div>
			{#if alertsEnabled}
				<div>
					<p class="text-sm font-medium text-fg mb-1">Error categories</p>
					<p class="text-sm text-fg-muted mb-2">
						{alertCategories.length === 0
							? 'None selected: every failure notifies.'
							: 'Only failures in the selected categories notify.'}
					</p>
					<div class="flex flex-wrap gap-1.5">
						{#each FAILURE_ALERT_CATEGORIES as category (category.value)}
							<button
								type="button"
								class="px-2 py-1 text-xs rounded border transition-colors {alertCategories.includes(
									category.value
								)
									? 'bg-signal/10 text-signal border-signal/25'
									: 'text-fg-muted border-line-strong hover:text-fg hover:border-line-hover'}"
								aria-pressed={alertCategories.includes(category.value)}
								onclick={() =>
									onSettingChange(
										'notify_admins_on_generation_failure_categories',
										toggleCategory(alertCategories, category.value)
									)}
							>
								{category.label}
							</button>
						{/each}
					</div>
				</div>
			{/if}
		</div>
	</div>
</DetailSection>
