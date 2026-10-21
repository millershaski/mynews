import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../repository.dart';
import '../widgets/common.dart';
import 'ai_screen.dart';

const String noBackgroundText = 'No background resource available';

/// One story: original coverage by publisher, background resources and the entry to the AI analysis.
class StoryScreen extends StatefulWidget {
  final String storyId;
  final NewsRepository repo;
  final StorySummary? initial;

  const StoryScreen({super.key, required this.storyId, required this.repo, this.initial});

  @override
  State<StoryScreen> createState() => _StoryScreenState();
}

class _StoryScreenState extends State<StoryScreen> {
  Loaded<StoryDetail>? _data;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final d = await widget.repo.story(widget.storyId);
      if (mounted) setState(() => _data = d);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } catch (_) {
      if (mounted) setState(() => _error = 'Something went wrong while loading this story.');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Story')),
      body: _body(context),
    );
  }

  Widget _body(BuildContext context) {
    final data = _data;
    if (data == null) {
      if (_loading) {
        final headline = widget.initial?.headline;
        return LoadingView(message: headline == null ? 'Loading story…' : 'Loading “$headline”…');
      }
      return ErrorView(message: _error ?? 'Could not load this story.', onRetry: _load);
    }
    final d = data.data;
    final theme = Theme.of(context);
    final byPublisher = d.articlesByPublisher;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: EdgeInsets.zero,
        children: [
          if (_loading) const LinearProgressIndicator(),
          if (data.offline) OfflineBanner(syncedAt: data.syncedAt, reason: data.offlineReason),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                OriginalText(d.summary.headline,
                    style: theme.textTheme.headlineSmall,
                    attribution: d.articles.isEmpty ? null : 'Headline from ${d.articles.first.publisherName}'),
                const SizedBox(height: 8),
                Text(
                  'Latest: ${formatLocal(d.summary.latestPublishedAt)} · '
                  '${d.summary.publisherCount} publisher${d.summary.publisherCount == 1 ? '' : 's'} · '
                  '${d.summary.articleCount} article${d.summary.articleCount == 1 ? '' : 's'}',
                  style: theme.textTheme.bodySmall,
                ),

                const SectionTitle('Original coverage'),
                Text('Opens the publisher\'s page (needs an internet connection).', style: theme.textTheme.bodySmall),
                const SizedBox(height: 8),
                Wrap(spacing: 8, runSpacing: 8, children: [
                  for (final entry in byPublisher.entries)
                    FilledButton.tonalIcon(
                      onPressed: () => entry.value.length == 1
                          ? openLink(context, entry.value.first.url)
                          : _choose(context, entry.key, entry.value),
                      icon: Icon(entry.value.length == 1 ? Icons.open_in_new : Icons.list),
                      label: Text(entry.value.length == 1 ? entry.key : '${entry.key} (${entry.value.length})'),
                    ),
                ]),

                const SectionTitle('AI analysis'),
                _AiEntry(detail: d),

                const SectionTitle('Background'),
                if (d.background.isEmpty)
                  Text(d.backgroundMessage ?? noBackgroundText, style: theme.textTheme.bodyMedium)
                else
                  Wrap(spacing: 8, runSpacing: 8, children: [
                    for (final b in d.background)
                      OutlinedButton.icon(
                        onPressed: () => openLink(context, b.url),
                        icon: const Icon(Icons.menu_book_outlined),
                        label: Text(b.label),
                      ),
                  ]),

                const SectionTitle('Feed excerpts'),
                Text('Original wording from each publisher\'s RSS feed, not the full article.',
                    style: theme.textTheme.bodySmall),
                for (final a in d.articles) _ArticleCard(article: a),
              ],
            ),
          ),
        ],
      ),
    );
  }

  void _choose(BuildContext context, String publisher, List<Article> articles) {
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (sheet) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Text('$publisher has ${articles.length} articles in this story',
                  style: Theme.of(sheet).textTheme.titleMedium),
            ),
            for (final a in articles)
              ListTile(
                leading: const Icon(Icons.article_outlined),
                title: OriginalText(a.title, maxLines: 3),
                subtitle: Text(formatLocal(a.publishedAt)),
                trailing: const Icon(Icons.open_in_new, size: 18),
                onTap: () {
                  Navigator.of(sheet).pop();
                  openLink(context, a.url);
                },
              ),
          ],
        ),
      ),
    );
  }
}

class _AiEntry extends StatelessWidget {
  final StoryDetail detail;
  const _AiEntry({required this.detail});

  @override
  Widget build(BuildContext context) {
    final ai = detail.ai;
    final scheme = Theme.of(context).colorScheme;
    return Card(
      color: scheme.tertiaryContainer.withValues(alpha: 0.45),
      margin: EdgeInsets.zero,
      child: ListTile(
        leading: Icon(Icons.auto_awesome, color: scheme.tertiary),
        title: const Text('Overview & perspectives'),
        subtitle: Text(ai.hasText
            ? '${ai.label} · ${formatLocal(ai.generatedAt)}${ai.status == 'updating' ? ' · update pending' : ''}'
            : (ai.message ?? 'Not available yet.')),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => AiScreen(detail: detail))),
      ),
    );
  }
}

class _ArticleCard extends StatelessWidget {
  final Article article;
  const _ArticleCard({required this.article});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: const EdgeInsets.only(top: 10),
      child: InkWell(
        onTap: () => openLink(context, article.url),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(
              '${article.publisherName} · ${formatLocal(article.publishedAt)}'
              '${article.dateEstimated ? ' (time estimated: feed gave no valid date)' : ''}',
              style: theme.textTheme.labelSmall,
            ),
            const SizedBox(height: 4),
            OriginalText(article.title, style: theme.textTheme.titleSmall),
            if (article.excerpt.isNotEmpty) ...[
              const SizedBox(height: 6),
              OriginalText('“${article.excerpt}”', style: theme.textTheme.bodyMedium),
            ],
          ]),
        ),
      ),
    );
  }
}
