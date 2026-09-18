import { ImageResponse } from 'next/og';
export async function GET(_request:Request,{params}:{params:Promise<{size:string}>}) {
  const {size:raw}=await params;const size=Number(raw);
  if(![180,192,512].includes(size))return new Response('Not found',{status:404});
  return new ImageResponse(<div style={{display:'flex',alignItems:'center',justifyContent:'center',width:'100%',height:'100%',background:'#f5efe3',color:'#725b35',fontSize:size*.36,fontWeight:600}}>BA</div>,{width:size,height:size});
}
