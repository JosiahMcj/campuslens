import { SAMPLE_CHARTS } from '../chartSamples'
import { AnswerChart } from './AnswerChart'

/** /dev/charts: every chart template drawn with sample data, for review and
 * screenshots. It reads nothing from the API. */
export function ChartGallery() {
  return (
    <main className="ac-gallery">
      <header>
        <h1>Answer chart templates</h1>
        <p className="hint">
          Sample data only. Every Explore answer uses one of these templates; the code picks it from
          the shape of the result and plugs the computed figures in. Withheld figures are gaps,
          never zeros.
        </p>
      </header>
      {SAMPLE_CHARTS.map((model) => (
        <AnswerChart key={model.template} model={model} />
      ))}
    </main>
  )
}
