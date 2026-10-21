import 'package:flutter/material.dart';

import 'repository.dart';
import 'screens/about_screen.dart';
import 'screens/explore_screen.dart';
import 'screens/latest_screen.dart';

void main() {
  runApp(MyNewsApp(repo: NewsRepository()));
}

class MyNewsApp extends StatelessWidget {
  final NewsRepository repo;
  const MyNewsApp({super.key, required this.repo});

  @override
  Widget build(BuildContext context) {
    ThemeData theme(Brightness b) => ThemeData(
          useMaterial3: true,
          colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF1E5AA8), brightness: b),
        );
    return MaterialApp(
      title: 'My News',
      debugShowCheckedModeBanner: false,
      theme: theme(Brightness.light),
      darkTheme: theme(Brightness.dark),
      home: HomeShell(repo: repo),
    );
  }
}

/// Bottom navigation between the globe (home), the latest grouped stories and the status page.
/// IndexedStack keeps each tab's state when switching.
class HomeShell extends StatefulWidget {
  final NewsRepository repo;
  const HomeShell({super.key, required this.repo});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(index: _tab, children: [
        ExploreScreen(repo: widget.repo),
        LatestScreen(repo: widget.repo),
        AboutScreen(repo: widget.repo),
      ]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.public_outlined), selectedIcon: Icon(Icons.public), label: 'Explore'),
          NavigationDestination(icon: Icon(Icons.newspaper_outlined), selectedIcon: Icon(Icons.newspaper), label: 'Latest'),
          NavigationDestination(icon: Icon(Icons.info_outline), selectedIcon: Icon(Icons.info), label: 'About'),
        ],
      ),
    );
  }
}
