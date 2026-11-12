import type { AcceptedKinds } from '$lib/kits/kitCounts';
import type { KitDetail } from '$lib/kits/types';

export type PickerConfirmLabel = string | ((info: { count: number; kitNames: string[] }) => string);

export interface MediaPickerKits {
	accepted?: AcceptedKinds;
	mediaRoom?: number | null;
	loadedIds?: string[];
	onLoad?: (kits: KitDetail[]) => void;
}
