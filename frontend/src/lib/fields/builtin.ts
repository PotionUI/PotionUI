/**
 * Registers every core (non-plugin) field component onto the field registry.
 * Mirrors `src/core/fields/builtin.py`'s type -> component table on the
 * backend - this file is the ONLY place core field type aliases are defined;
 * `FormField.svelte` no longer branches on `type` at all.
 */
import { registerFieldComponent } from './registry';
import TextInput from '$lib/components/form-fields/TextInput.svelte';
import NumberInput from '$lib/components/form-fields/NumberInput.svelte';
import SliderField from '$lib/components/form-fields/SliderField.svelte';
import SeedField from '$lib/components/form-fields/SeedField.svelte';
import CheckboxField from '$lib/components/form-fields/CheckboxField.svelte';
import SelectField from '$lib/components/form-fields/SelectField.svelte';
import CarouselField from '$lib/components/form-fields/CarouselField.svelte';
import ResolutionField from '$lib/components/form-fields/ResolutionField.svelte';
import CheckboxGroupField from '$lib/components/form-fields/CheckboxGroupField.svelte';
import ModelField from '$lib/components/form-fields/ModelField.svelte';
import LoraPickerField from '$lib/components/form-fields/LoraPickerField.svelte';
import MediaLoaderField from '$lib/components/form-fields/MediaLoaderField.svelte';
import LLMField from '$lib/components/form-fields/LLMField.svelte';
import TabsField from '$lib/components/form-fields/TabsField.svelte';
import AccordionField from '$lib/components/form-fields/AccordionField.svelte';
import GroupField from '$lib/components/form-fields/GroupField.svelte';
import RowField from '$lib/components/form-fields/RowField.svelte';
import AlertField from '$lib/components/form-fields/AlertField.svelte';
import MarkdownField from '$lib/components/form-fields/MarkdownField.svelte';
import HeaderField from '$lib/components/form-fields/HeaderField.svelte';
import SectionField from '$lib/components/form-fields/SectionField.svelte';
import GateField from '$lib/components/form-fields/GateField.svelte';
import PromptTimelineField from '$lib/components/form-fields/PromptTimelineField.svelte';
import CameraShotField from '$lib/components/form-fields/CameraShotField.svelte';

let registered = false;

/** Idempotent - safe to call multiple times (e.g. in tests). */
export function registerBuiltinFieldComponents(): void {
	if (registered) return;
	registered = true;

	const table: Record<string, any> = {
		string: TextInput,
		textbox: TextInput,
		number: NumberInput,
		integer: NumberInput,
		stepper: NumberInput,
		slider: SliderField,
		seed: SeedField,
		boolean: CheckboxField,
		checkbox: CheckboxField,
		select: SelectField,
		carousel: CarouselField,
		resolution: ResolutionField,
		checkbox_group: CheckboxGroupField,
		model: ModelField,
		models: ModelField,
		lora_picker: LoraPickerField,
		image: MediaLoaderField,
		video: MediaLoaderField,
		audio: MediaLoaderField,
		media: MediaLoaderField,
		llm: LLMField,
		tabs: TabsField,
		accordion: AccordionField,
		group: GroupField,
		row: RowField,
		alert: AlertField,
		markdown: MarkdownField,
		header: HeaderField,
		section: SectionField,
		gate: GateField,
		prompt_timeline: PromptTimelineField,
		camera_shot: CameraShotField
	};

	for (const [type, component] of Object.entries(table)) {
		registerFieldComponent(type, { component });
	}
}
