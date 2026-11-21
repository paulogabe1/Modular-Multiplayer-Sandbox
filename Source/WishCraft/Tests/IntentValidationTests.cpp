#include "Abilities/LoadoutValidator.h"
#include "Abilities/AbilityTokenTable.h"
#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FWishCraftIntentValidationTest,
	"WishCraft.Intent.Validation", EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FWishCraftIntentValidationTest::RunTest(const FString& Parameters)
{
	UAbilityTokenTable* Table = NewObject<UAbilityTokenTable>();
	FAbilityTokenDefinition Fireball;
	FAbilityParamRange Damage;
	Damage.Min = 5.f;
	Damage.Max = 100.f;
	Damage.Default = 20.f;
	Fireball.ParamRanges.Add(TEXT("Damage"), Damage);
	Fireball.DefaultParams.Add(TEXT("Damage"), 20.f);
	FAbilityParamRange Multiplier;
	Multiplier.Min = 1.05f;
	Multiplier.Max = 3.f;
	Multiplier.Default = 1.6f;
	Fireball.ParamRanges.Add(TEXT("Multiplier"), Multiplier);
	Fireball.DefaultParams.Add(TEXT("Multiplier"), 1.6f);
	Table->Tokens.Add(TEXT("Fireball"), Fireball);
	FAbilityTokenDefinition Defense;
	Defense.Category = EAbilityCategory::Defense;
	Table->Tokens.Add(TEXT("Shield"), Defense);
	Table->Tokens.Add(TEXT("Invisibility"), Defense);

	const TCHAR* Labels[] = { TEXT("much_lower"), TEXT("lower"), TEXT("default"), TEXT("higher"), TEXT("max") };
	const float Expected[] = { 5.f, 10.f, 20.f, 40.f, 100.f };
	for (int32 Index = 0; Index < 5; ++Index)
	{
		const FString Json = FString::Printf(TEXT("{\"tokens\":[{\"name\":\"Fireball\",\"params\":{\"Damage\":\"%s\"}}]}"), Labels[Index]);
		const FLoadoutValidationResult Result = ULoadoutValidator::ValidateIntentJson(Table, Json);
		TestTrue(Labels[Index], Result.bValid);
		if (Result.Specs.Num() == 1)
		{
			TestEqual(TEXT("Bucket maps through the authoritative range"), Result.Specs[0].GetParam(TEXT("Damage")), Expected[Index]);
			TestEqual(TEXT("Omitted parameter keeps its default"), Result.Specs[0].GetParam(TEXT("Multiplier")), 1.6f);
		}
		else { AddError(TEXT("Expected exactly one validated spec")); }
	}
	const FLoadoutValidationResult Clamped = ULoadoutValidator::ValidateIntentJson(Table,
		TEXT("{\"tokens\":[{\"name\":\"Fireball\",\"params\":{\"Multiplier\":\"higher\"}}]}"));
	TestTrue(TEXT("Clamped intent is valid"), Clamped.bValid);
	if (Clamped.Specs.Num() == 1)
	{
		TestEqual(TEXT("Twice default is clamped to maximum"), Clamped.Specs[0].GetParam(TEXT("Multiplier")), 3.f);
	}
	const TCHAR* Invalid[] = {
		TEXT("not json"),
		TEXT("{}"),
		TEXT("{\"tokens\":[]}"),
		TEXT("{\"tokens\":[null]}"),
		TEXT("{\"tokens\":[{\"name\":\"Unknown\",\"params\":{}}]}"),
		TEXT("{\"tokens\":[{\"name\":\"Fireball\",\"params\":{\"Damage\":99999}}]}"),
		TEXT("{\"tokens\":[{\"name\":\"Fireball\",\"params\":{\"Damage\":\"unlimited\"}}]}"),
		TEXT("{\"tokens\":[{\"name\":\"Fireball\",\"params\":{\"Unknown\":\"max\"}}]}"),
		TEXT("{\"tokens\":[{\"name\":\"Fireball\",\"params\":{}},{\"name\":\"Fireball\",\"params\":{}}]}"),
		TEXT("{\"tokens\":[{\"name\":\"Shield\",\"params\":{}},{\"name\":\"Invisibility\",\"params\":{}}]}"),
		TEXT("{\"tokens\":[{\"name\":\"Fireball\",\"params\":{}}],\"ruleset_live\":true}"),
		TEXT("{\"tokens\":[{\"name\":\"Fireball\",\"params\":{},\"value\":99999}]}")
	};
	for (const TCHAR* Json : Invalid)
	{
		const FLoadoutValidationResult Result = ULoadoutValidator::ValidateIntentJson(Table, Json);
		TestFalse(Json, Result.bValid);
		TestTrue(TEXT("Rejected intent has no publishable specs"), Result.Specs.IsEmpty());
		TestFalse(TEXT("Rejected intent explains why"), Result.Error.IsEmpty());
	}
	TestFalse(TEXT("Missing table is rejected"), ULoadoutValidator::ValidateIntentJson(nullptr, TEXT("{} ")).bValid);
	return true;
}
#endif
