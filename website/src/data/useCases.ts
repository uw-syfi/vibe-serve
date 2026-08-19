/**
 * Single source of truth for the homepage's use-case tabs and the
 * architecture diagram they drive. Selecting a use case swaps the
 * diagram's USER INPUT values and BESPOKE SYSTEM output/metric; the loop
 * machinery in between never changes, which is the point.
 */

export type DiagramOutputLineKind = 'dir' | 'file' | 'file-new' | 'file-modified' | 'note';

export type DiagramOutputLine = {
  text: string;
  kind: DiagramOutputLineKind;
};

export type DiagramSpec = {
  /** Hardware vendor logo to highlight in the Hardware card's logo row. */
  hardwareVendor?: 'nvidia' | 'amd' | 'intel' | 'apple' | 'amazon';
  startingPoint: string[];
  hardware: string[];
  workload: string[];
  output: DiagramOutputLine[];
};

export type UseCase = {
  id: string;
  tag: string;
  headline: string;
  description: string;
  metric: string;
  /** Pre-wrapped lines (SVG text can't reflow on its own). */
  metricLabel: string[];
  link?: {label: string; href: string};
  diagram: DiagramSpec;
};

export const GENERIC_DIAGRAM: DiagramSpec = {
  startingPoint: ['Model, weights,', 'or a checkout'],
  hardware: ['Device spec'],
  workload: ['Benchmark metrics'],
  output: [
    {text: 'serving_system/', kind: 'dir'},
    {text: '├─ api_server.py', kind: 'file'},
    {text: '├─ scheduler.py', kind: 'file'},
    {text: '├─ router.py', kind: 'file'},
    {text: '├─ cache.py', kind: 'file'},
    {text: '├─ model.py', kind: 'file'},
    {text: '└─ backend.py', kind: 'file'},
    {text: 'tests/', kind: 'dir'},
    {text: '├─ test_accuracy.py', kind: 'file'},
    {text: '└─ test_router.py', kind: 'file'},
    {text: 'bench/', kind: 'dir'},
    {text: '├─ microbench.py', kind: 'file'},
    {text: '├─ latency.csv', kind: 'file'},
    {text: '└─ tpt.csv', kind: 'file'},
  ],
};

const PAPER_LINK = {label: 'Read the paper', href: 'https://arxiv.org/abs/2605.06068'};

export const USE_CASES: UseCase[] = [
  {
    id: 'code-edit-spec-decode',
    tag: 'FROM SCRATCH · WORKLOAD',
    headline: 'Speculative decoding for code edits',
    description:
      'Generic servers ignore the predicted-output field. VibeSys treats it as a speculative-decoding draft.',
    metric: '5.95×',
    metricLabel: ['vs. vanilla autoregressive ·', '2× over vLLM spec-dec'],
    link: PAPER_LINK,
    diagram: {
      hardwareVendor: 'nvidia',
      startingPoint: ['Qwen3-32B', 'from scratch'],
      hardware: ['1× H100'],
      workload: ['CodeEditorBench,', 'predicted-outputs'],
      output: [
        {text: 'serving_system/', kind: 'dir'},
        {text: '├─ api_server.py', kind: 'file'},
        {text: '├─ scheduler.py', kind: 'file'},
        {text: '├─ predicted_verifier.py', kind: 'file-new'},
        {text: '├─ cache.py', kind: 'file'},
        {text: '├─ model.py', kind: 'file'},
        {text: '└─ backend.py', kind: 'file'},
        {text: 'tests/', kind: 'dir'},
        {text: '├─ test_accuracy.py', kind: 'file'},
        {text: '└─ test_router.py', kind: 'file'},
        {text: 'bench/', kind: 'dir'},
        {text: '├─ microbench.py', kind: 'file'},
        {text: '└─ latency.csv', kind: 'file'},
      ],
    },
  },
  {
    id: 'show-o2-macbook',
    tag: 'FROM SCRATCH · HARDWARE',
    headline: 'Image generation on a laptop',
    description:
      'Show-o2 runs on no generic stack at all. VibeSys ports it to MLX, within 7% of the theoretical peak.',
    metric: '6.27×',
    metricLabel: ['vs. PyTorch-MPS baseline'],
    link: PAPER_LINK,
    diagram: {
      hardwareVendor: 'apple',
      startingPoint: ['Show-o2 1.5B-HQ', 'from scratch'],
      hardware: ['MacBook (M3 Pro)'],
      workload: ['432×432', 'text-to-image'],
      output: [
        {text: 'serving_system/', kind: 'dir'},
        {text: '├─ api_server.py', kind: 'file'},
        {text: '├─ diffusion_head.py', kind: 'file'},
        {text: '├─ body.py', kind: 'file'},
        {text: '├─ tokenizer.py', kind: 'file'},
        {text: '└─ mlx_backend.py', kind: 'file'},
        {text: 'tests/', kind: 'dir'},
        {text: '├─ test_accuracy.py', kind: 'file'},
        {text: '└─ test_image_shape.py', kind: 'file'},
        {text: 'bench/', kind: 'dir'},
        {text: '├─ microbench.py', kind: 'file'},
        {text: '└─ latency.csv', kind: 'file'},
      ],
    },
  },
  {
    id: 'vllm-spec-decode',
    tag: 'STARTS FROM YOUR STACK',
    headline: 'In-place vLLM optimization',
    description: 'VibeSys inspects a running vLLM and adds speculative decoding, no rewrite needed.',
    metric: '4.7×',
    metricLabel: ['throughput over the', 'starting config'],
    diagram: {
      hardwareVendor: 'nvidia',
      startingPoint: ['vLLM (pinned)', 'Llama-3.3-70B, bf16'],
      hardware: ['2× H100'],
      workload: ['Chat/completion'],
      output: [
        {text: 'vllm/  (existing)', kind: 'dir'},
        {text: '~ engine/spec_decode.py', kind: 'file-modified'},
        {text: '~ worker/model_runner.py', kind: 'file-modified'},
        {text: '├─ api_server.py', kind: 'file'},
        {text: '├─ worker/gpu_worker.py', kind: 'file'},
        {text: '└─ ... (untouched)', kind: 'file'},
        {text: 'tests/', kind: 'dir'},
        {text: '└─ test_accuracy.py', kind: 'file'},
        {text: 'bench/', kind: 'dir'},
        {text: '└─ latency.csv', kind: 'file'},
      ],
    },
  },
  {
    id: 'mpmc-queue',
    tag: 'BEYOND SERVING',
    headline: 'Concurrent queue optimization',
    description: 'VibeSys cuts cross-thread cache-coherence traffic in a concurrent queue.',
    metric: '2.77×',
    metricLabel: ["vs. oneTBB's concurrent_queue"],
    diagram: {
      hardwareVendor: 'intel',
      startingPoint: ['MPMC queue', 'naive Rust seed'],
      hardware: ['Multi-core CPU'],
      workload: ['Throughput +', 'FIFO correctness'],
      output: [
        {text: 'queue-rs/', kind: 'dir'},
        {text: '├─ src/lib.rs', kind: 'file'},
        {text: '└─ queue-candidate.so', kind: 'file'},
        {text: 'benches/', kind: 'dir'},
        {text: '└─ mpmc_bench.rs', kind: 'file'},
        {text: 'same C ABI, any language', kind: 'note'},
      ],
    },
  },
];
