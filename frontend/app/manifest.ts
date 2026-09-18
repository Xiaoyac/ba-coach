import type { MetadataRoute } from 'next';
export default function manifest():MetadataRoute.Manifest {
  return {id:'/',name:'BA Coach',short_name:'BA Coach',start_url:'/',scope:'/',display:'standalone',
    background_color:'#faf7f0',theme_color:'#faf7f0',lang:'zh-CN',
    icons:[{src:'/manifest-icon/192',sizes:'192x192',type:'image/png'},
      {src:'/manifest-icon/512',sizes:'512x512',type:'image/png'}]};
}
