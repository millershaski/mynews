import 'package:flutter/material.dart';

import '../models.dart';
import '../widgets/common.dart';
import 'perspective_screen.dart';

const String insufficientPerspectivesText = 'Insufficient evidence for distinct perspectives';

/// AI overview (agreement, disagreement/uncertainty) and the list of perspectives for one story.
/// The text is generated once on the server and shared by every reader.
class AiScreen extends StatelessWidget {
  final StoryDetail detail;
  const AiScreen({super.key, required this.detail});

  @override
  Widget build(BuildContext context) {
    final ai = detail.ai;
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('AI overview')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
        children: [
          Text('Story', style: theme.textTheme.labelMedium),
          OriginalText(detail.summary.headline, style: theme.textTheme.titleMedium),
          const SizedBox(height: 16),
          if (!ai.hasText)
            _Unavailable(ai: ai)
          else ...[
            if (ai.status == 'updating' && ai.message != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Text(ai.message!, style: theme.textTheme.bodySmall),
              ),
            AiBox(
              label: ai.label,
              generatedAt: ai.generatedAt,
              disclosure: ai.disclosure,
              children: [
                Text('Overview', style: theme.textTheme.titleSmall),
                const SizedBox(height: 4),
                Text(ai.overview!),
                if (ai.agreement != null) ...[
                  const SizedBox(height: 12),
                  Text('Where sources agree', style: theme.textTheme.titleSmall),
                  const SizedBox(height: 4),
                  Text(ai.agreement!),
                ],
                if (ai.disagreementOrUncertainty != null) ...[
                  const SizedBox(height: 12),
                  Text('Disagreement or uncertainty', style: theme.textTheme.titleSmall),
                  const SizedBox(height: 4),
                  Text(ai.disagreementOrUncertainty!),
                ],
                const SizedBox(height: 12),
                SourceList(ai.overviewSources, title: 'Based on these articles'),
              ],
            ),
            const SectionTitle('Perspectives'),
            if (ai.perspectivesStatus == 'supported' && ai.perspectives.length >= 2)
              for (final p in ai.perspectives)
                Card(
                  margin: const EdgeInsets.only(bottom: 8),
                  child: ListTile(
                    leading: Icon(Icons.forum_outlined, color: theme.colorScheme.tertiary),
                    title: Text(p.label),
                    subtitle: Text(
                      '${p.sources.length} source${p.sources.length == 1 ? '' : 's'} · AI-generated',
                      style: theme.textTheme.bodySmall,
                    ),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => PerspectiveScreen(perspective: p, ai: ai, headline: detail.summary.headline),
                    )),
                  ),
                )
            else
              Text(ai.perspectivesMessage ?? insufficientPerspectivesText, style: theme.textTheme.bodyMedium),
            const SizedBox(height: 20),
            Text(
              'Model: ${ai.model ?? 'unknown'} · Prompt version: ${ai.promptVersion ?? 'unknown'}\n'
              'Generated once on the server and shared with every reader. It is regenerated only when the '
              'story\'s articles change.',
              style: theme.textTheme.bodySmall,
            ),
          ],
        ],
      ),
    );
  }
}

class _Unavailable extends StatelessWidget {
  final StoryAi ai;
  const _Unavailable({required this.ai});

  @override
  Widget build(BuildContext context) {
    final (icon, title) = switch (ai.status) {
      'failed' => (Icons.error_outline, 'AI overview unavailable'),
      'outdated' => (Icons.update, 'AI overview is out of date'),
      _ => (Icons.hourglass_empty, 'AI overview not generated yet'),
    };
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(icon),
            const SizedBox(width: 8),
            Expanded(child: Text(title, style: Theme.of(context).textTheme.titleSmall)),
          ]),
          const SizedBox(height: 8),
          Text(ai.message ?? 'No AI text is available for this story right now.'),
          const SizedBox(height: 8),
          Text(
            'The app never makes up a summary. The original coverage on the story page is always available.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ]),
      ),
    );
  }
}
