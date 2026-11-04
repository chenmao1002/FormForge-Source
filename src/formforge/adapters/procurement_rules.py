"""Bounded native-value checks, explicit placement reviews and Decimal estimates."""
from collections import Counter
from datetime import datetime,timedelta,timezone
from decimal import Decimal,ROUND_CEILING
import re


D=lambda value:Decimal(str(value))


def native_requirements(value,category):
    """Only recognized passive notation; annotations do not invent requirements."""
    text=value.strip();token=text.split()[0];result={}
    if category=='resistor':
        token=re.sub(r'(?:Ω|[oO][hH][mM][sS]?)$','',token)
        embedded=re.fullmatch(r'(\d+)([RrKkMm])(\d+)',token)
        normal=re.fullmatch(r'(\d+(?:\.\d+)?)([RrKkMm]?)',token)
        if embedded:number=D(embedded[1]+'.'+embedded[3]);unit=embedded[2]
        elif normal:number=D(normal[1]);unit=normal[2]
        else:raise ValueError('不能明确解析原生电阻值：'+value)
        result['resistanceOhm']=number*{'':D(1),'R':D(1),'r':D(1),'k':D(1000),'K':D(1000),'M':D(1000000),'m':D('.001')}[unit]
    elif category=='capacitor':
        embedded=re.fullmatch(r'(\d+)([pPnNuUµμm])(\d+)[fF]?',token)
        normal=re.fullmatch(r'(\d+(?:\.\d+)?)([pPnNuUµμm]?)([fF]?)',token)
        if embedded:number=D(embedded[1]+'.'+embedded[3]);unit=embedded[2]
        elif normal and (normal[2] or normal[3]):number=D(normal[1]);unit=normal[2]
        else:raise ValueError('不能明确解析原生电容值：'+value)
        result['capacitanceF']=number*{'':D(1),'p':D('1e-12'),'P':D('1e-12'),'n':D('1e-9'),'N':D('1e-9'),'u':D('1e-6'),'U':D('1e-6'),'µ':D('1e-6'),'μ':D('1e-6'),'m':D('.001')}[unit]
        dielectric=re.findall(r'\b(?:X[5-9][A-Z]|C0G|NP0|Y5V)\b',text)
        if dielectric:result['dielectric']=dielectric[0]
        voltages=re.findall(r'(?<![\w.])(\d+(?:\.\d+)?)\s*V\b',text,re.I)
        if len(voltages)>1:raise ValueError('多个电压标记不能自动推断要求。')
        if voltages:result['ratedVoltageV']=D(voltages[0])
    tolerance=re.findall(r'(\d+(?:\.\d+)?)\s*%',text)
    if len(tolerance)>1:raise ValueError('多个公差标记不能自动推断要求。')
    if tolerance:result['tolerancePercent']=D(tolerance[0])
    power=re.findall(r'(?<![\w.])(\d+(?:\.\d+)?)\s*(m?W)\b',text)
    if len(power)>1:raise ValueError('多个功率标记不能自动推断要求。')
    if power:result['ratedPowerW']=D(power[0][0])*(D('.001') if power[0][1]=='mW' else D(1))
    return result


def timestamp(value):return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)


def offer_status(offer,now,max_age,quantity):
    if not offer:return dict(state='missing',fresh=False,priceAvailable=False,stockSufficient=None)
    observed=timestamp(offer['observedAt']);expiry=observed+timedelta(seconds=max_age)
    if offer.get('validUntil'):expiry=min(expiry,timestamp(offer['validUntil']))
    future=observed>now+timedelta(seconds=300);fresh=not future and now<=expiry
    result=dict(state='future-invalid' if future else 'fresh-declaration' if fresh else 'stale',fresh=fresh,
        observedAt=offer['observedAt'],effectiveExpiry=expiry.isoformat(),currency=offer['currency'],priceAvailable=False,
        stockQuantity=offer['stockQuantity'],stockSufficient=None,minimumOrderQuantity=offer['minimumOrderQuantity'],orderMultiple=offer['orderMultiple'],
        source=offer['evidence'],inventoryReserved=False,officialQuote=False)
    if quantity is None:return result
    if offer['minimumOrderQuantity'] is None or offer['orderMultiple'] is None:
        result.update(requiredUnits=quantity,purchaseUnits=None,orderConstraintsComplete=False,
            stockSufficient=False if offer['stockQuantity'] is not None and offer['stockQuantity']<quantity else None)
        return result
    units=int((D(max(quantity,offer['minimumOrderQuantity']))/D(offer['orderMultiple'])).to_integral_value(rounding=ROUND_CEILING))*offer['orderMultiple']
    tiers=[p for p in offer['prices'] if p['minimumQuantity']<=units]
    result.update(requiredUnits=quantity,purchaseUnits=units,orderConstraintsComplete=True,stockSufficient=None if offer['stockQuantity'] is None else offer['stockQuantity']>=units)
    if tiers:
        price=D(tiers[-1]['unitPrice']);result.update(priceAvailable=True,unitPrice=str(price),materialsSubtotal=str(price*units),priceBreakMinimum=tiers[-1]['minimumQuantity'])
    return result


def evaluate(basis,params,parts,now=None):
    now=now or datetime.now(timezone.utc);problems=[];warnings=[]
    def issue(code,message,reference=None):problems.append(dict(code=code,message=message,reference=reference))
    native={f['reference']:f for f in basis['native']['footprints']};targets={p['reference']:p for p in basis['report']['selectedParts']}
    rows=[];seen=set();skus={};commercial={};unresolved=[];used_parts=set()
    requested=[r for line in params['lines'] for r in line['references']]
    if len(set(requested))!=len(requested) or set(requested)!=set(targets):issue('coverage','采购计划须恰好覆盖基准场景全部所选位号。')
    if params['targetFormat']=='jlcpcb' and len({r.upper() for r in targets})!=len(targets):issue('provider_case_collision','JLCPCB将位号转大写，当前位号会冲突。')
    placements={p['reference']:p for p in params['placements']}
    if len(placements)!=len(params['placements']) or not set(placements)<=set(targets):issue('placement_reference','质心/方向审核位号重复或不属于所选范围。')
    for index,line in enumerate(params['lines']):
        refs=line['references'];part=parts.get(line.get('partSourceId'));used_parts.add(line['partSourceId']) if part else None
        if line['disposition']=='unresolved':
            unresolved.extend(refs)
            for ref in refs:
                target=targets.get(ref,{})
                rows.append(dict(reference=ref,disposition='unresolved',reason=line['reason'],partSourceId=None,nativeValue=target.get('value'),footprint=target.get('footprint'),mounting=target.get('mounting'),nativePosition=target.get('position')))
            if any(r in placements for r in refs):issue('unresolved_placement','未确定器件不接受质心/方向审核。')
            continue
        if part is None:issue('part_source','所选器件资料缺失。');continue
        if not part['mpn'].strip() or not part['manufacturer'].strip():issue('part_identity','厂家/MPN不能空白。')
        supplier_key=(part['supplier'].casefold(),(part.get('supplierPartNumber') or part['mpn']).casefold())
        identity=(part['manufacturer'],part['mpn'],line['partSourceId'])
        if supplier_key in skus and skus[supplier_key]!=identity:issue('sku_collision','同供应商料号对应不同器件或不同事实版本。')
        skus[supplier_key]=identity
        for ref in refs:
            if ref not in targets:continue
            target=targets[ref];fp=native[ref];counts=Counter(p['number'] for p in fp['pads']);bindings={p['boardPad']:len(p['partTerminals']) for p in part['padBindings']}
            if bindings!=dict(counts):issue('pad_mapping','端子/重复接点映射与原生焊盘不一致。',ref)
            if part['packageFootprint']!=target['footprint']:issue('footprint','资料兼容封装与冻结PCB封装不一致。',ref)
            symbol=basis['symbols'][ref]['part'];category='resistor' if symbol in {'R','R_Small'} else 'capacitor' if symbol in {'C','C_Small'} else 'exact-device'
            if part['category']!=category:issue('part_category','器件分类不能绕过原生符号的参数核对。',ref)
            requirements={}
            if category=='exact-device':
                if target['value'] not in part['designValues']:issue('device_identity','未声明对应原设计型号/订货变体；功能替料须先改原图重审。',ref)
            else:
                try:requirements=native_requirements(target['value'],category)
                except ValueError as error:issue('unsupported_value',str(error),ref)
                for key,value in requirements.items():
                    selected=part['facts'].get(key)
                    if selected is None:issue('missing_rating','资料缺少原图明确参数：'+key,ref);continue
                    if key=='dielectric':okay=selected==value
                    elif key=='tolerancePercent':okay=D(selected)<=value
                    elif key in {'ratedPowerW','ratedVoltageV'}:okay=D(selected)>=value
                    else:okay=D(selected)==value
                    if not okay:issue('rating_mismatch','所选器件不满足原图明确参数：'+key,ref)
            review=placements.get(ref);position=target['position'];supplier_position=None
            if review and position:
                supplier_position=dict(xMm=str(D(position['PosX'])+D(review['deltaXmm'])),yMm=str(D(position['PosY'])+D(review['deltaYmm'])),
                    rotationDegrees=str(((D(position['Rot'])+D(review['rotationAdjustmentDegrees']))%D(360)+D(360))%D(360)),layer='Top' if position['Side']=='top' else 'Bottom',
                    adjustment=review,frame='native-position-export-mm; Y already negated; explicit board-frame centroid offset')
            rows.append(dict(reference=ref,disposition=line['disposition'],reason=line['reason'],partSourceId=line['partSourceId'],manufacturer=part['manufacturer'],mpn=part['mpn'],supplier=part['supplier'],
                supplierPartNumber=part.get('supplierPartNumber'),packageName=part['packageName'],footprint=target['footprint'],nativeValue=target['value'],mounting=target['mounting'],nativePosition=position,
                parsedRequirements={k:str(v) for k,v in requirements.items()},supplierPosition=supplier_position,declaredPinMapping=part['padBindings']))
        if line['disposition']=='selected':
            item=commercial.setdefault(supplier_key,dict(partSourceId=line['partSourceId'],part=part,references=[],requiredUnits=None if params['boardQuantity'] is None else 0,extraUnits=0))
            item['references']+=refs;item['extraUnits']+=line['extraUnits']
            if item['requiredUnits'] is not None:item['requiredUnits']+=len(refs)*params['boardQuantity']+line['extraUnits']
    complete=bool(targets) and not unresolved and len(rows)==len(targets) and not problems
    if params['mode']=='reviewed' and not complete:issue('review_incomplete','已审核模式要求全部所选器件都有有效料号资料。')
    estimates=[];totals={};estimate_ready=bool(commercial)
    for item in commercial.values():
        status=offer_status(item['part'].get('offer'),now,params['maxOfferAgeSeconds'],item['requiredUnits'])
        if status['state']=='future-invalid':issue('future_offer','价格/库存观察时间明显在未来。')
        if status['state'] in {'missing','stale'}:warnings.append(dict(code='offer_refresh',references=item['references'],message='商业快照缺失或已过期，不能用于当前材料估算。'))
        ready=status['fresh'] and status['priceAvailable'] and status['stockSufficient'] is True
        estimate_ready=estimate_ready and ready
        if ready:totals[status['currency']]=totals.get(status['currency'],D(0))+D(status['materialsSubtotal'])
        estimates.append(dict(references=sorted(item['references']),partSourceId=item['partSourceId'],requiredUnits=item['requiredUnits'],extraUnits=item['extraUnits'],snapshot=status,usableForIndicativeEstimate=ready))
    supplier_mapping=all(row.get('supplierPartNumber') and row.get('supplier','').lower() in {'jlcpcb','lcsc'} or row['disposition']=='customer-supplied' for row in rows if row['disposition']!='unresolved')
    placements_complete=bool(rows) and all(row.get('supplierPosition') for row in rows)
    checked=not problems
    return dict(checksPassed=checked,planComplete=complete and checked,procurementReviewed=params['mode']=='reviewed' and complete and checked,
        reviewTrust='external-agent/caller declared; service checks immutable evidence, native identity and listed constraints',
        rows=sorted(rows,key=lambda r:r['reference']),problems=problems,warnings=warnings,
        selectedReferenceCount=len(targets),resolvedReferenceCount=sum(row['disposition']!='unresolved' and row['reference'] in targets for row in rows),unresolvedReferences=sorted(set(unresolved)),
        supplierMappingComplete=complete and supplier_mapping,placementReviewComplete=placements_complete,
        supplierFilesReady=checked and complete and params['mode']=='reviewed' and params['targetFormat']=='jlcpcb' and supplier_mapping and placements_complete,
        matchingFilesReady=checked and bool(rows) and params['targetFormat']=='jlc-smt-matching',
        matchingPurpose='part-selection-only; not production placement approval' if params['targetFormat']=='jlc-smt-matching' else None,
        matchingUnreviewedCenters=sorted(row['reference'] for row in rows if not row.get('supplierPosition')) if params['targetFormat']=='jlc-smt-matching' else [],
        indicativeMaterialsEstimateComplete=checked and complete and estimate_ready,commercial=estimates,materialsSubtotalsByCurrency={k:str(v) for k,v in totals.items()},
        quantitiesIncludeOnlyExplicitExtraUnits=True,estimateExcludes=['PCB','assembly','tax','shipping','supplier-final-attrition'],
        evaluatedAt=now.isoformat(),nativeChecks=basis['report']['nativeChecks'],engineeringChecksPassed=False,
        downstreamEngineeringReviewRequired=True,platformPartMatchingVerified=False,platformPreviewVerified=False,officialQuote=False,
        readyForQuote=False,readyToSubmit=False,manufacturingApproved=False,hardwareVerified=False)
