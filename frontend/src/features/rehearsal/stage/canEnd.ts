/**
 * 끝내기를 열어도 되는 때. 끝까지 하지 않은 발표는 BE 가 중간 종료로 구분하지 않고 점수까지 남겨
 * 평가 기준이 흐려지므로 마지막 장에서만 엽니다 (#695). 장 수를 모르면(0) 못 끝내는 일이 없게 엽니다.
 */
export function canEnd(pageCount: number, slideNumber: number): boolean {
  return pageCount === 0 || slideNumber >= pageCount;
}
