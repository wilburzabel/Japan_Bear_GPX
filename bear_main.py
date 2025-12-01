# ... (在 "folium.PolyLine(...).add_to(m)" 这行代码的下面插入) ...

            # === [可选功能] 绘制橙色预警范围 ===
            # 1. 计算缓冲区几何图形
            # 注意：这里需要先把 points 转为 (Lon, Lat) 给 Shapely 计算
            shapely_line_points = [(p[1], p[0]) for p in points]
            route_line_geom = LineString(shapely_line_points)
            
            # 简单估算：1度 ≈ 90,000米 (日本纬度)
            deg_buffer = buffer_radius_m / 90000.0
            raw_buffer = route_line_geom.buffer(deg_buffer)
            
            # 2. 关键步骤：简化多边形 (防止让地图变卡/消失)
            # tolerance=0.0005 约等于 50米精度，视觉上看不出区别，但能极大减少数据量
            simplified_buffer = raw_buffer.simplify(tolerance=0.0005)
            
            # 3. 画到地图上
            folium.GeoJson(
                simplified_buffer,
                style_function=lambda x: {
                    'fillColor': 'orange', 
                    'color': 'orange', 
                    'weight': 1, 
                    'fillOpacity': 0.15 # 很淡的橙色，不遮挡视线
                }
            ).add_to(m)
            
            # ... (接着是 "# --- D. 计算危险点 ---") ...
