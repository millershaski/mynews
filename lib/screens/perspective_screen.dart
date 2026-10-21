import 'package:flutter/material.dart';

import '../models.dart';
import '../widgets/common.dart';

/// One AI-described perspective, with its evidence limits and the articles it rests on.
class PerspectiveScreen extends StatelessWidget {
  final Perspective perspective;
  final StoryAi ai;
  final String headline;

  const PerspectiveScreen({super.key, required this.perspective, required this.ai, required this.headline});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Perspective')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
        children: [
          Text('Story', style: theme.textTheme.labelMedium),
          OriginalText(headline, style: theme.textTheme.titleMedium),
          const SizedBox(height: 16),
          AiBox(
            label: ai.label,
            generatedAt: ai.generatedAt,
            disclosure: ai.disclosure,
            children: [
              Text(perspective.label, style: theme.textTheme.titleMedium),
              const SizedBox(height: 8),
              Text(perspective.summary),
              if (perspective.evidenceLimits.isNotEmpty) ...[
                const SizedBox(height: 12),
                Text('Limits of the evidence', style: theme.textTheme.titleSmall),
                const SizedBox(height: 4),
                Text(perspective.evidenceLimits),
              ],
              const SizedBox(height: 12),
              SourceList(perspective.sources, title: 'Articles behind this perspective'),
            ],
          ),
        ],
      ),
    );
  }
}
