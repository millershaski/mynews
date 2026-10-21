import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../repository.dart';
import '../widgets/common.dart';
import 'story_screen.dart';

/// Latest grouped stories from the last 7 days, newest first.
class LatestScreen extends StatefulWidget {
  final NewsRepository repo;
  const LatestScreen({super.key, required this.repo});

  @override
  State<LatestScreen> createState() => _LatestScreenState();
}

class _LatestScreenState extends State<LatestScreen> {
  Loaded<StoryList>? _data;
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
      final d = await widget.repo.stories();
      if (!mounted) return;
      setState(() => _data = d);
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _error = e.message);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = 'Something went wrong while loading stories.');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Latest stories'),
        actions: [
          IconButton(
            tooltip: 'Refresh',
            onPressed: _loading ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: _body(),
    );
  }

  Widget _body() {
    final data = _data;
    if (data == null) {
      if (_loading) return const LoadingView(message: 'Loading stories…\nThe free server may take up to a minute to wake up.');
      return ErrorView(message: _error ?? 'Could not load stories.', onRetry: _load);
    }
    final list = data.data;
    return Column(children: [
      if (_loading) const LinearProgressIndicator(),
      if (data.offline) OfflineBanner(syncedAt: data.syncedAt, reason: data.offlineReason),
      if (_error != null && !data.offline)
        MaterialBanner(
          content: Text(_error!),
          actions: [TextButton(onPressed: _load, child: const Text('Retry'))],
        ),
      Expanded(
        child: RefreshIndicator(
          onRefresh: _load,
          child: list.stories.isEmpty
              ? EmptyView(
                  message: 'No stories from the last 7 days yet.\nNews is collected every hour.',
                  onRefresh: _load,
                )
              : ListView.separated(
                  physics: const AlwaysScrollableScrollPhysics(),
                  itemCount: list.stories.length + 1,
                  separatorBuilder: (_, _) => const Divider(height: 1),
                  itemBuilder: (context, i) {
                    if (i == 0) return _Header(list: list);
                    return StoryTile(story: list.stories[i - 1], repo: widget.repo);
                  },
                ),
        ),
      ),
    ]);
  }
}

class _Header extends StatelessWidget {
  final StoryList list;
  const _Header({required this.list});

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
        child: Text(
          '${list.stories.length} stories from the last ${list.windowHours ~/ 24} days · newest first\n'
          'Last collected: ${formatLocal(list.lastSuccessfulCollectionAt)}',
          style: Theme.of(context).textTheme.bodySmall,
        ),
      );
}

class StoryTile extends StatelessWidget {
  final StorySummary story;
  final NewsRepository repo;
  const StoryTile({super.key, required this.story, required this.repo});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final pubs = story.publisherCount == 1 ? '1 publisher' : '${story.publisherCount} publishers';
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      title: OriginalText(story.headline, style: theme.textTheme.titleMedium, maxLines: 3),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 6),
        child: Wrap(spacing: 12, runSpacing: 4, children: [
          _Meta(icon: Icons.schedule, text: '${formatAgo(story.latestPublishedAt)} · ${formatLocal(story.latestPublishedAt)}'),
          _Meta(icon: Icons.newspaper, text: pubs),
        ]),
      ),
      trailing: const Icon(Icons.chevron_right),
      onTap: () => Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => StoryScreen(storyId: story.id, repo: repo, initial: story)),
      ),
    );
  }
}

class _Meta extends StatelessWidget {
  final IconData icon;
  final String text;
  const _Meta({required this.icon, required this.text});

  @override
  Widget build(BuildContext context) => Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 14, color: Theme.of(context).hintColor),
        const SizedBox(width: 4),
        Text(text, style: Theme.of(context).textTheme.bodySmall),
      ]);
}
