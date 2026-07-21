import QueryWorkbench from './components/QueryWorkbench'

export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>DecisionHarbor</h1>
        <p>受治理数据分析平台</p>
      </header>
      <main>
        <QueryWorkbench />
      </main>
    </div>
  )
}
