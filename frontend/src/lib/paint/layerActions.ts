export interface LayerAction {
	id: string;
	label: string;
	icon: string;
	run: (index: number) => void;
}
