import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:url_launcher/url_launcher.dart';

import '../models.dart';

// ---------------------------------------------------------------------------------------------
// Time formatting. The server sends UTC; the app shows local time and says so.

String formatLocal(DateTime? t) {
  if (t == null) return 'unknown time';
  return DateFormat('EEE d MMM, HH:mm').format(t.toLocal());
}

String formatAgo(DateTime? t, {DateTime? now}) {
  if (t == null) return 'unknown time';
  final d = (now ?? DateTime.now()).toUtc().difference(t.toUtc());
  if (d.isNegative || d.inMinutes < 1) return 'just now';
  if (d.inMinutes < 60) return '${d.inMinutes} min ago';
  if (d.inHours < 24) return '${d.inHours} h ago';
  return '${d.inDays} d ago';
}

// ---------------------------------------------------------------------------------------------
// Links. Publisher pages always need a network connection, even when the app shows cached news.

Future<void> openLink(BuildContext context, String url) async {
  final uri = Uri.tryParse(url);
  final messenger = ScaffoldMessenger.of(context);
  var ok = false;
  if (uri != null && (uri.scheme == 'https' || uri.scheme == 'http')) {
    try {
      ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
    } catch (_) {
      ok = false;
    }
  }
  if (!ok) {
    messenger.showSnackBar(const SnackBar(
      content: Text('Could not open the page. Publisher pages need an internet connection.'),
    ));
  }
}

// ---------------------------------------------------------------------------------------------
// Visual separation of original publisher wording and AI wording.

/// Publisher text (headlines, feed excerpts): serif, quoted, with the publisher named.
class OriginalText extends StatelessWidget {
  final String text;
  final String? attribution;
  final TextStyle? style;
  final int? maxLines;

  const OriginalText(this.text, {super.key, this.attribution, this.style, this.maxLines});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final base = (style ?? theme.textTheme.bodyMedium)!.copyWith(fontFamily: 'serif');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(text, style: base, maxLines: maxLines, overflow: maxLines == null ? null : TextOverflow.ellipsis),
        if (attribution != null)
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text(attribution!, style: theme.textTheme.labelSmall?.copyWith(color: theme.hintColor)),
          ),
      ],
    );
  }
}

/// Small tag shown on every block of AI wording.
class AiTag extends StatelessWidget {
  final String label;
  final DateTime? generatedAt;

  const AiTag({super.key, this.label = 'AI-generated', this.generatedAt});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Wrap(
      crossAxisAlignment: WrapCrossAlignment.center,
      spacing: 8,
      runSpacing: 4,
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
          decoration: BoxDecoration(color: scheme.tertiary, borderRadius: BorderRadius.circular(12)),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.auto_awesome, size: 14, color: scheme.onTertiary),
            const SizedBox(width: 4),
            Text(label, style: TextStyle(color: scheme.onTertiary, fontSize: 12, fontWeight: FontWeight.w600)),
          ]),
        ),
        if (generatedAt != null)
          Text('Generated ${formatLocal(generatedAt)}', style: Theme.of(context).textTheme.labelSmall),
      ],
    );
  }
}

/// Container for AI wording: tinted background, left accent bar, label and disclosure.
class AiBox extends StatelessWidget {
  final String label;
  final DateTime? generatedAt;
  final String? disclosure;
  final List<Widget> children;

  const AiBox({super.key, required this.children, this.label = 'AI-generated', this.generatedAt, this.disclosure});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      decoration: BoxDecoration(
        color: scheme.tertiaryContainer.withValues(alpha: 0.45),
        borderRadius: BorderRadius.circular(12),
        border: Border(left: BorderSide(color: scheme.tertiary, width: 4)),
      ),
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      // Transparent Material so tappable source tiles draw their ink above the tinted background.
      child: Material(
        type: MaterialType.transparency,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AiTag(label: label, generatedAt: generatedAt),
            const SizedBox(height: 10),
            ...children,
            if (disclosure != null && disclosure!.isNotEmpty) ...[
              const SizedBox(height: 10),
              Text(disclosure!,
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(fontStyle: FontStyle.italic)),
            ],
          ],
        ),
      ),
    );
  }
}

/// Lists the source articles behind an AI statement; each opens the original page.
class SourceList extends StatelessWidget {
  final List<SourceRef> sources;
  final String title;

  const SourceList(this.sources, {super.key, this.title = 'Sources'});

  @override
  Widget build(BuildContext context) {
    if (sources.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title, style: Theme.of(context).textTheme.titleSmall),
        for (final s in sources)
          ListTile(
            dense: true,
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.article_outlined),
            title: OriginalText(s.title, maxLines: 2),
            subtitle: Text(s.publisherName),
            trailing: const Icon(Icons.open_in_new, size: 18),
            onTap: () => openLink(context, s.url),
          ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------------------------
// States.

class OfflineBanner extends StatelessWidget {
  final DateTime? syncedAt;
  final String? reason;

  const OfflineBanner({super.key, this.syncedAt, this.reason});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Material(
      color: scheme.secondaryContainer,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        child: Row(children: [
          Icon(Icons.cloud_off, color: scheme.onSecondaryContainer),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              'Offline mode: showing news saved ${syncedAt == null ? 'earlier' : 'on ${formatLocal(syncedAt)}'}. '
              'Stories older than 7 days are hidden. Opening publisher pages needs a connection.',
              style: TextStyle(color: scheme.onSecondaryContainer),
            ),
          ),
        ]),
      ),
    );
  }
}

class ErrorView extends StatelessWidget {
  final String message;
  final VoidCallback? onRetry;

  const ErrorView({super.key, required this.message, this.onRetry});

  @override
  Widget build(BuildContext context) => _CenteredMessage(
        icon: Icons.error_outline,
        message: message,
        action: onRetry == null ? null : FilledButton.icon(
          onPressed: onRetry,
          icon: const Icon(Icons.refresh),
          label: const Text('Try again'),
        ),
      );
}

class EmptyView extends StatelessWidget {
  final String message;
  final VoidCallback? onRefresh;

  const EmptyView({super.key, required this.message, this.onRefresh});

  @override
  Widget build(BuildContext context) => _CenteredMessage(
        icon: Icons.inbox_outlined,
        message: message,
        action: onRefresh == null ? null : OutlinedButton.icon(
          onPressed: onRefresh,
          icon: const Icon(Icons.refresh),
          label: const Text('Refresh'),
        ),
      );
}

class LoadingView extends StatelessWidget {
  final String? message;
  const LoadingView({super.key, this.message});

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const CircularProgressIndicator(),
            if (message != null) ...[
              const SizedBox(height: 16),
              Text(message!, textAlign: TextAlign.center),
            ],
          ]),
        ),
      );
}

class _CenteredMessage extends StatelessWidget {
  final IconData icon;
  final String message;
  final Widget? action;

  const _CenteredMessage({required this.icon, required this.message, this.action});

  @override
  Widget build(BuildContext context) => Center(
        child: SingleChildScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(24),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(icon, size: 48, color: Theme.of(context).hintColor),
            const SizedBox(height: 12),
            Text(message, textAlign: TextAlign.center),
            if (action != null) ...[const SizedBox(height: 16), action!],
          ]),
        ),
      );
}

class SectionTitle extends StatelessWidget {
  final String text;
  const SectionTitle(this.text, {super.key});

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 20, bottom: 8),
        child: Text(text, style: Theme.of(context).textTheme.titleMedium),
      );
}
