<script lang="ts">
	import FormField from '$lib/components/form-fields/FormField.svelte';
	import PromptSegment from '$lib/components/PromptSegment.svelte';
	import { registerBuiltinFieldComponents } from '$lib/fields/builtin';
	import type { ChipData, Segment } from '$lib/types/segments';
	import { Badge } from '$lib/components/ui';
	import ComponentExample from './ComponentExample.svelte';

	registerBuiltinFieldComponents();

	let fieldValues: Record<string, unknown> = {
		subject: 'Amber glass potion bottle',
		lighting: 'studio',
		guidance: 6.5,
		high_quality: true
	};

	const schemaGroup = {
		type: 'group',
		title: 'Render controls',
		children: [
			{
				type: 'row',
				children: [
					{
						name: 'subject',
						type: 'string',
						title: 'Subject',
						description: 'The primary subject from the preset schema.'
					},
					{
						name: 'lighting',
						type: 'select',
						title: 'Lighting',
						options: [
							{ value: 'studio', label: 'Studio softbox' },
							{ value: 'window', label: 'Window light' },
							{ value: 'dramatic', label: 'Dramatic rim light' }
						]
					}
				]
			},
			{
				name: 'guidance',
				type: 'slider',
				title: 'Prompt guidance',
				description: 'How closely the output follows the prompt.',
				minimum: 1,
				maximum: 12,
				step: 0.5,
				tooltip: 'This configuration is supplied by the active preset.'
			},
			{
				name: 'high_quality',
				type: 'checkbox',
				title: 'High quality pass',
				description: 'Adds the preset-defined refinement pass.'
			}
		]
	};

	let positiveSegment: Segment = {
		id: 'kit-positive',
		content: 'Editorial product photograph of an amber potion bottle on polished stone',
		type: 'content',
		chips: {}
	};

	let negativeSegment: Segment = {
		id: 'kit-negative',
		content: 'blurry, low contrast, illegible label, warped glass',
		type: 'content',
		chips: {}
	};

	let linkedSegment: Segment = {
		id: 'kit-linked',
		content: 'Soft directional window light with a warm reflected fill',
		type: 'content',
		chips: {},
		name: 'Warm window light',
		color: '#F59E0B',
		description: 'Detached rich segment metadata'
	};

	let disabledSegment: Segment = {
		id: 'kit-disabled',
		content: 'Dense atmospheric fog behind the product',
		type: 'content',
		chips: {},
		isDisabled: true
	};

	function changeField(name: string, value: unknown) {
		fieldValues = { ...fieldValues, [name]: value };
	}

	function updateContent(target: 'positive' | 'negative' | 'linked' | 'disabled', content: string) {
		if (target === 'positive') positiveSegment = { ...positiveSegment, content };
		if (target === 'negative') negativeSegment = { ...negativeSegment, content };
		if (target === 'linked') linkedSegment = { ...linkedSegment, content };
		if (target === 'disabled') disabledSegment = { ...disabledSegment, content };
	}

	function updateChips(target: 'positive' | 'negative' | 'linked' | 'disabled', chips: Record<string, ChipData>) {
		if (target === 'positive') positiveSegment = { ...positiveSegment, chips };
		if (target === 'negative') negativeSegment = { ...negativeSegment, chips };
		if (target === 'linked') linkedSegment = { ...linkedSegment, chips };
		if (target === 'disabled') disabledSegment = { ...disabledSegment, chips };
	}

	function toggleDisabled() {
		disabledSegment = { ...disabledSegment, isDisabled: !disabledSegment.isDisabled };
	}

</script>

<div class="space-y-8">
	<ComponentExample
		title="Schema-driven field composition"
		description="Production fields rendered from a local YAML-shaped schema fixture. The renderer knows only generic group, row, and field types; ordering, labels, options, and presentation metadata remain preset-owned."
		code={`<FormField\n  name={null}\n  config={presetSchemaGroup}\n  value={formData}\n  onChange={updateField}\n/>`}
	>
		<div class="w-full max-w-3xl rounded-lg border border-line bg-surface-1 p-4 sm:p-5">
			<FormField name={null} config={schemaGroup} value={fieldValues} onChange={changeField} />
		</div>
	</ComponentExample>

	<ComponentExample
		title="Prompt segment states"
		description="The production segment card across positive, negative, rich-metadata, and disabled states. All examples use local data and remain interactive."
		code={`<PromptSegment\n  {segment}\n  index={0}\n  isNegative={false}\n  on:contentChange={updateContent}\n  on:toggleDisabled={toggleDisabled}\n/>`}
	>
		<div class="grid w-full grid-cols-1 gap-5 xl:grid-cols-2">
			<div class="min-w-0 space-y-2">
				<div class="flex items-center justify-between gap-2">
					<p class="text-xs font-semibold text-fg">Positive</p>
					<Badge variant="success" size="sm">Included</Badge>
				</div>
				<PromptSegment
					segment={positiveSegment}
					index={0}
					total={1}
					on:contentChange={(event) => updateContent('positive', event.detail)}
					on:chipsChange={(event) => updateChips('positive', event.detail)}
				/>
			</div>

			<div class="min-w-0 space-y-2">
				<div class="flex items-center justify-between gap-2">
					<p class="text-xs font-semibold text-fg">Negative</p>
					<Badge variant="danger" size="sm">Excluded</Badge>
				</div>
				<PromptSegment
					segment={negativeSegment}
					index={0}
					total={1}
					isNegative
					on:contentChange={(event) => updateContent('negative', event.detail)}
					on:chipsChange={(event) => updateChips('negative', event.detail)}
				/>
			</div>

			<div class="min-w-0 space-y-2">
				<div class="flex items-center justify-between gap-2">
					<p class="text-xs font-semibold text-fg">Rich metadata</p>
					<Badge variant="warning" size="sm">Named</Badge>
				</div>
				<PromptSegment
					segment={linkedSegment}
					index={1}
					total={1}
					on:contentChange={(event) => updateContent('linked', event.detail)}
					on:chipsChange={(event) => updateChips('linked', event.detail)}
				/>
			</div>

			<div class="min-w-0 space-y-2">
				<div class="flex items-center justify-between gap-2">
					<p class="text-xs font-semibold text-fg">Disabled</p>
					<Badge variant="neutral" size="sm">Not submitted</Badge>
				</div>
				<PromptSegment
					segment={disabledSegment}
					index={2}
					total={1}
					on:contentChange={(event) => updateContent('disabled', event.detail)}
					on:chipsChange={(event) => updateChips('disabled', event.detail)}
					on:toggleDisabled={toggleDisabled}
				/>
			</div>
		</div>
	</ComponentExample>
</div>
