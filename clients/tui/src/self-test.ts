import {TextRenderable} from '@opentui/core';
import {createTestRenderer} from '@opentui/core/testing';

const testRenderer = await createTestRenderer({width: 20, height: 4});
try {
  testRenderer.renderer.root.add(
    new TextRenderable(testRenderer.renderer, {
      id: 'vibesys-self-test',
      content: 'vibesys',
    }),
  );
  const frame = await testRenderer.waitForFrame(value => value.includes('vibesys'));
  if (!frame.includes('vibesys')) throw new Error('OpenTUI did not render the test frame');
} finally {
  testRenderer.renderer.destroy();
}
