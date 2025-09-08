import { useState } from 'react'
import reactLogo from './assets/react.svg'
import viteLogo from '/vite.svg'
import './App.css'
import StudentGradingSystem from './StudentGradingSystem'

function App() {
  const [count, setCount] = useState(0)

  return (
    <>
      <StudentGradingSystem/>
    </>
  )
}

export default App
