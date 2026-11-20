type Listener = () => void;

export class Registry<T extends { id: string }> {
	private items = new Map<string, T>();
	private listeners = new Set<Listener>();

	register(item: T): () => void {
		this.items.set(item.id, item);
		this.emit();
		return () => {
			if (this.items.get(item.id) === item) {
				this.items.delete(item.id);
				this.emit();
			}
		};
	}

	get(id: string): T | undefined {
		return this.items.get(id);
	}

	has(id: string): boolean {
		return this.items.has(id);
	}

	list(): T[] {
		return [...this.items.values()];
	}

	subscribe(listener: Listener): () => void {
		this.listeners.add(listener);
		return () => {
			this.listeners.delete(listener);
		};
	}

	private emit(): void {
		for (const listener of this.listeners) listener();
	}
}
