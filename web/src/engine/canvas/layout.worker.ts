import { layeredLayout } from "./layout";

self.onmessage = (event: MessageEvent<Parameters<typeof layeredLayout>>) => {
  const [topology, hints] = event.data;
  self.postMessage(layeredLayout(topology, hints));
};
